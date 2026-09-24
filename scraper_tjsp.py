"""
Robô de Consulta Processual - TJSP (e-SAJ + EPROC)
Utiliza undetected-chromedriver para contornar proteções anti-bot.
Chamado pelo backend Node.js via child_process.
Retorna JSON via stdout para o Node consumir.
"""

import sys
import json
import time
import os
import re

try:
    import undetected_chromedriver as uc
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC
    from bs4 import BeautifulSoup, NavigableString
except ImportError as e:
    print(json.dumps({"error": f"Dependência não instalada: {e}. Execute: pip install undetected-chromedriver beautifulsoup4 selenium"}))
    sys.exit(1)

def scrape_eproc(numero_cnj, driver):
    """
    Tenta buscar o processo no sistema EPROC do TJSP.
    """
    try:
        print(f"[*] Buscando no EPROC: {numero_cnj}")
        driver.get("https://eproc-consulta.tjsp.jus.br/consulta_1g/externo_controlador.php?acao=tjsp@consulta_unificada_publica/consultar")
        
        wait = WebDriverWait(driver, 15)
        
        # Tenta localizar o campo de número do processo com múltiplos seletores
        campo = None
        for selector in ["txtNumProcesso", "txtNumeroProcesso", "txtProcesso"]:
            try:
                campo = wait.until(EC.presence_of_element_located((By.ID, selector)))
                if campo: break
            except: continue
        
        if not campo:
            campo = driver.find_element(By.NAME, "txtNumProcesso")
            
        campo.clear()
        campo.send_keys(numero_cnj)
        
        # Clica em Consultar
        botao = None
        for selector in ["btnConsultar", "btnPesquisar"]:
            try:
                botao = driver.find_element(By.ID, selector)
                if botao: break
            except: continue
            
        if not botao:
            botao = driver.find_element(By.CSS_SELECTOR, "button[type='submit']")
            
        botao.click()

        # O EPROC protege a consulta com captcha. Se o clique dispara o alerta
        # "Aguarde a verificação do captcha", a busca NÃO aconteceu — não dá
        # para seguir e devolver um processo vazio como se fosse sucesso.
        try:
            alerta = driver.switch_to.alert
            texto_alerta = alerta.text
            alerta.accept()
            print(f"[!] EPROC bloqueou a consulta: {texto_alerta}")
            return {"error": f"EPROC exige captcha: {texto_alerta}"}
        except Exception:
            pass  # sem alerta = seguiu adiante

        # Aguarda a página de resultados carregar (procura pela tabela de eventos ou mensagem de erro)
        try:
            wait.until(lambda d: "tabelaEventos" in d.page_source or "Nenhum registro encontrado" in d.page_source or "Resultado da Consulta" in d.page_source)
        except:
            time.sleep(5) # Fallback

        if "Nenhum registro encontrado" in driver.page_source:
            return None

        # Nenhum evento extraído significa que a leitura falhou (captcha, layout
        # novo, sessão barrada) — devolver zero movimentações faria o CRM exibir
        # "processo sem andamento", que é bem pior do que um erro explícito.
        if not driver.find_elements(By.ID, "tabelaEventos"):
            print("[!] EPROC: página de resultados sem tabela de eventos.")
            return {"error": "EPROC não devolveu a tabela de eventos (captcha ou layout alterado)"}


        # Extração de dados
        soup = BeautifulSoup(driver.page_source, 'html.parser')
        
        classe = "N/A"
        assunto = "N/A"
        
        # No EPROC, as informações costumam estar em labels seguidos de spans ou em tabelas
        for label in soup.find_all(["label", "th"]):
            txt = label.get_text().strip()
            if "Classe" in txt:
                val = label.find_next(["span", "td", "div"])
                if val: classe = val.get_text().strip()
            elif "Assunto" in txt:
                val = label.find_next(["span", "td", "div"])
                if val: assunto = val.get_text().strip()

        movimentacoes = []
        tabela = soup.find("table", {"id": "tabelaEventos"})
        if tabela:
            for tr in tabela.find_all("tr")[1:15]: # Pega até as 14 últimas
                tds = tr.find_all("td")
                if len(tds) >= 3:
                    data = tds[1].get_text().strip()
                    desc = tds[2].get_text().strip()
                    # Limpa espaços excessivos
                    desc = " ".join(desc.split())
                    movimentacoes.append({"dataHora": data, "nome": desc})
            
        return {
            "numero": numero_cnj,
            "classe": classe,
            "assunto": assunto,
            "partes": [], # TODO: Implementar extração de partes no EPROC se necessário
            "movimentacoes": movimentacoes,
            "fonte": "EPROC-TJSP"
        }
    except Exception as e:
        print(f"[!] Erro ao buscar no EPROC: {e}")
        return None

RE_DATA = re.compile(r"^\d{2}/\d{2}/\d{4}$")

# Como o e-SAJ rotula cada parte, por polo.
POLO_ATIVO = ("reqte", "requerente", "autor", "exeqte", "exequente",
              "impetrante", "embargte", "apelante", "credor", "agravante")
POLO_PASSIVO = ("reqdo", "reqda", "requerido", "requerida", "réu", "reu",
                "executado", "impetrado", "embargdo", "apelado", "devedor",
                "agravado")


def polo_da_parte(tipo):
    """"Reqte" → ATIVO, "Reqda" → PASSIVO. Devolve o rótulo cru se não reconhecer."""
    t = tipo.strip().lower().rstrip(".:")
    if any(t.startswith(p) for p in POLO_ATIVO):
        return "ATIVO"
    if any(t.startswith(p) for p in POLO_PASSIVO):
        return "PASSIVO"
    return tipo.strip().upper()


def expandir_movimentacoes(driver):
    """
    Faz o e-SAJ carregar as movimentações.

    A página do processo NÃO traz as movimentações no HTML inicial: ela vem com
    um botão "Exibir movimentações" e um <div id="containerMovimentacoes">
    vazio, preenchido depois por AJAX. Sem este clique a extração volta sempre
    zerada — por mais correto que esteja o seletor da tabela.
    """
    try:
        container = driver.find_element(By.ID, "containerMovimentacoes")
    except Exception:
        return  # layout antigo: as movimentações já vêm prontas na página

    tamanho_inicial = len(container.get_attribute("innerHTML") or "")

    try:
        botao = driver.find_element(By.ID, "btnExibirMovimentacoes")
    except Exception:
        return  # já expandido

    try:
        driver.execute_script("arguments[0].click();", botao)
        WebDriverWait(driver, 30).until(
            lambda d: len(
                d.find_element(By.ID, "containerMovimentacoes").get_attribute("innerHTML") or ""
            ) > tamanho_inicial + 200
        )
        print("[*] Movimentações expandidas.")
    except Exception as e:
        print(f"[!] Não consegui expandir as movimentações: {e}")


def extrair_movimentacoes(soup, limite=30):
    """
    Movimentações do e-SAJ, da mais recente para a mais antiga.

    Cada linha vem como [data, (vazio), descrição]; a validação da data descarta
    cabeçalho e linhas de espaçamento. Não confundir com a tabela "Petições
    diversas", que também é de datas e fica na mesma página.
    """
    tabela = None
    container = soup.find(id="containerMovimentacoes")
    if container:
        tabela = container.find("table")
    if tabela is None:
        # Layouts antigos do e-SAJ traziam a tabela com id próprio.
        tabela = (soup.find(id="tabelaTodasMovimentacoes")
                  or soup.find(id="tabelaUltimasMovimentacoes"))
    if tabela is None:
        return []

    movimentacoes = []
    for linha in tabela.find_all("tr"):
        tds = linha.find_all("td")
        if len(tds) < 2:
            continue
        data = " ".join(tds[0].get_text().split())
        if not RE_DATA.match(data):
            continue
        descricao = " ".join(tds[-1].get_text().split())
        if descricao:
            movimentacoes.append({"dataHora": data, "nome": descricao})
        if len(movimentacoes) >= limite:
            break
    return movimentacoes


def extrair_partes(soup):
    """
    Partes do processo, de #tablePartesPrincipais.

    Cada linha é: <span class="tipoDeParticipacao">Reqte</span> e um td
    .nomeParteEAdvogado com o nome da parte, um <br>, e então "Advogado:" +
    nome do advogado. O nome da parte é o texto solto antes do primeiro <br>.
    """
    tabela = soup.find(id="tablePartesPrincipais") or soup.find(id="tableTodasPartes")
    if not tabela:
        return []

    partes = []
    for linha in tabela.find_all("tr"):
        tipo_el = linha.find("span", class_="tipoDeParticipacao")
        celula = linha.find("td", class_="nomeParteEAdvogado")
        if not tipo_el or not celula:
            continue

        nome = ""
        for filho in celula.contents:
            if getattr(filho, "name", None) == "br":
                break
            if isinstance(filho, NavigableString):
                texto = " ".join(str(filho).split())
                if texto:
                    nome = texto
                    break

        advogados = []
        for span in celula.find_all("span", class_="mensagemExibindo"):
            seguinte = span.next_sibling
            if seguinte:
                adv = " ".join(str(seguinte).split())
                if adv:
                    advogados.append(adv)

        tipo = " ".join(tipo_el.get_text().split())
        if nome:
            partes.append({
                "nome": nome,
                "polo": polo_da_parte(tipo),
                "tipo": tipo,
                "advogados": advogados,
            })
    return partes


def versao_chrome_instalada():
    """
    Descobre a versão major do Chrome instalado, para casar o ChromeDriver.

    Fixar a versão na mão quebra o robô a cada atualização do Chrome
    ("This version of ChromeDriver only supports Chrome version N").
    Ordem: env CHROME_VERSION_MAIN → registro do Windows → None (o
    undetected-chromedriver detecta sozinho).
    """
    override = os.environ.get("CHROME_VERSION_MAIN")
    if override:
        try:
            return int(override)
        except ValueError:
            pass

    try:
        import winreg
        for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
            try:
                with winreg.OpenKey(hive, r"SOFTWARE\Google\Chrome\BLBeacon") as chave:
                    versao = winreg.QueryValueEx(chave, "version")[0]
                    return int(versao.split(".")[0])
            except OSError:
                continue
    except ImportError:
        pass

    return None


def consultar_esaj(numero_cnj):
    """
    Consulta um processo no e-SAJ do TJSP usando o número CNJ.
    """
    clean = numero_cnj.replace("-", "").replace(".", "")
    if len(clean) != 20:
        return {"error": "CNJ inválido - deve ter 20 dígitos"}

    # Formato e-SAJ: NNNNNNN-DD.AAAA.J.TR.OOOO
    primeira_parte = f"{clean[0:7]}-{clean[7:9]}.{clean[9:13]}"
    foro = clean[16:20]

    options = uc.ChromeOptions()
    if os.environ.get('HEADLESS_MODE') == 'true':
        options.add_argument('--headless')
    
    options.add_argument('--no-sandbox')
    options.add_argument('--disable-dev-shm-usage')
    options.add_argument('--window-size=1920,1080')

    driver = None
    try:
        # Casa o ChromeDriver com o Chrome instalado na máquina
        driver = uc.Chrome(options=options, version_main=versao_chrome_instalada())
        wait = WebDriverWait(driver, 20)

        # ─── TENTATIVA 1: e-SAJ (Consulta Padrão) ───
        url = "https://esaj.tjsp.jus.br/cpopg/open.do"
        driver.get(url)
        
        try:
            campo_numero = wait.until(EC.presence_of_element_located((By.ID, "numeroDigitoAnoUnificado")))
            campo_numero.clear()
            campo_numero.send_keys(primeira_parte)

            campo_foro = driver.find_element(By.ID, "foroNumeroUnificado")
            campo_foro.clear()
            campo_foro.send_keys(foro)

            botao = driver.find_element(By.ID, "botaoConsultarProcessos")
            botao.click()
            
            # Aguarda resultado ou erro
            wait.until(lambda d: "classeProcesso" in d.page_source or "Não existem informações disponíveis" in d.page_source or "Mensagem" in d.page_source)
        except Exception as e:
            print(f"[*] Falha na busca inicial e-SAJ: {e}")

        # Se não encontrou na primeira tentativa, tenta modo "Outros" (CNJ Completo)
        if "Não existem informações disponíveis" in driver.page_source or "numeroDigitoAnoUnificado" in driver.page_source:
            print("[*] Não encontrado na busca padrão e-SAJ. Tentando via CNJ completo...")
            driver.get(url)
            try:
                radio_outros = wait.until(EC.element_to_be_clickable((By.ID, "radioOutros")))
                radio_outros.click()
                campo_outros = wait.until(EC.presence_of_element_located((By.ID, "dadosConsulta.valorConsulta")))
                campo_outros.clear()
                campo_outros.send_keys(numero_cnj)
                driver.find_element(By.ID, "botaoConsultarProcessos").click()
                
                # Aguarda resultado
                wait.until(lambda d: "classeProcesso" in d.page_source or "Não existem informações disponíveis" in d.page_source)
            except: pass

        # Se ainda não encontrou, tenta EPROC
        if "Não existem informações disponíveis" in driver.page_source or "numeroDigitoAnoUnificado" in driver.page_source:
            print("[*] Tentando fallback para o sistema EPROC...")
            eproc_res = scrape_eproc(numero_cnj, driver)
            if eproc_res:
                return eproc_res
            else:
                return {"error": "Processo não encontrado nos sistemas TJSP (e-SAJ/EPROC)"}

        # Extração de dados (e-SAJ)
        # As movimentações só existem no DOM depois deste clique.
        expandir_movimentacoes(driver)

        soup = BeautifulSoup(driver.page_source, 'html.parser')

        classe = soup.find(id="classeProcesso").get_text().strip() if soup.find(id="classeProcesso") else "N/A"
        assunto = soup.find(id="assuntoProcesso").get_text().strip() if soup.find(id="assuntoProcesso") else "N/A"

        movimentacoes = extrair_movimentacoes(soup)
        partes = extrair_partes(soup)

        return {
            "numero": numero_cnj,
            "classe": classe,
            "assunto": assunto,
            "partes": partes,
            "movimentacoes": movimentacoes,
            "fonte": "e-SAJ-TJSP"
        }

    except Exception as e:
        return {"error": str(e)}
    finally:
        if driver:
            driver.quit()

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(json.dumps({"error": "Número do processo não fornecido"}))
        sys.exit(1)
        
    resultado = consultar_esaj(sys.argv[1])
    print(json.dumps(resultado, ensure_ascii=False))
