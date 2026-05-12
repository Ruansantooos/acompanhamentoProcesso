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

try:
    import undetected_chromedriver as uc
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC
    from bs4 import BeautifulSoup
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
        time.sleep(5)
        
        # Tenta localizar o campo de número do processo
        try:
            campo = WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.ID, "txtNumeroProcesso")))
        except:
            campo = driver.find_element(By.NAME, "txtNumeroProcesso")
            
        campo.clear()
        campo.send_keys(numero_cnj)
        
        print("[*] Aguardando 5s para o CAPTCHA do EPROC...")
        time.sleep(5)
        
        # Clica em Consultar
        try:
            botao = driver.find_element(By.ID, "btnConsultar")
        except:
            botao = driver.find_element(By.CSS_SELECTOR, "button[type='submit']")
            
        botao.click()
        time.sleep(10)
        
        if "Nenhum registro encontrado" in driver.page_source:
            return None
            
        # Extração básica
        html = driver.page_source
        soup = BeautifulSoup(html, 'html.parser')
        
        # No EPROC as informações são estruturadas de forma diferente
        # Tentamos pegar o cabeçalho
        classe = "N/A"
        assunto = "N/A"
        
        # Procura por labels comuns no EPROC
        for label in soup.find_all("label"):
            txt = label.get_text().strip()
            if "Classe" in txt:
                parent = label.find_parent("div") or label.find_parent("td")
                if parent:
                    classe = parent.get_text().replace(txt, "").strip()
            elif "Assunto" in txt:
                parent = label.find_parent("div") or label.find_parent("td")
                if parent:
                    assunto = parent.get_text().replace(txt, "").strip()

        movimentacoes = []
        # Tabela de eventos típica do EPROC (TRF4/TJSP)
        tabela = soup.find("table", {"id": "tabelaEventos"})
        if tabela:
            for tr in tabela.find_all("tr")[1:11]: # Top 10
                tds = tr.find_all("td")
                if len(tds) >= 3:
                    movimentacoes.append({
                        "data": tds[1].get_text().strip(),
                        "descricao": tds[2].get_text().strip()
                    })
            
        return {
            "numero": numero_cnj,
            "classe": classe,
            "assunto": assunto,
            "movimentacoes": movimentacoes,
            "fonte": "EPROC-TJSP"
        }
    except Exception as e:
        print(f"[!] Erro ao buscar no EPROC: {e}")
        return None

def consultar_esaj(numero_cnj):
    """
    Consulta um processo no e-SAJ do TJSP usando o número CNJ.
    Retorna um dicionário com classe, partes e movimentações.
    """
    clean = numero_cnj.replace("-", "").replace(".", "")
    if len(clean) != 20:
        return {"error": "CNJ inválido - deve ter 20 dígitos"}

    # Formato e-SAJ: NNNNNNN-DD.AAAA.J.TR.OOOO
    primeira_parte = f"{clean[0:7]}-{clean[7:9]}.{clean[9:13]}"
    foro = clean[16:20]

    options = uc.ChromeOptions()
    # options.add_argument('--headless') # Headless costuma falhar mais em passar bot detection
    options.add_argument('--no-sandbox')
    options.add_argument('--disable-dev-shm-usage')
    options.add_argument('--window-size=1920,1080')

    driver = None
    try:
        driver = uc.Chrome(options=options)
        wait = WebDriverWait(driver, 20)

        # ─── TENTATIVA 1: e-SAJ ───
        url = "https://esaj.tjsp.jus.br/cpopg/open.do"
        driver.get(url)
        time.sleep(7)

        campo_numero = wait.until(EC.presence_of_element_located((By.ID, "numeroDigitoAnoUnificado")))
        campo_numero.clear()
        campo_numero.send_keys(primeira_parte)

        campo_foro = driver.find_element(By.ID, "foroNumeroUnificado")
        campo_foro.clear()
        campo_foro.send_keys(foro)

        print("[*] Aguardando 5s para o CAPTCHA (e-SAJ)...")
        time.sleep(5)

        botao = driver.find_element(By.ID, "botaoConsultarProcessos")
        botao.click()
        time.sleep(10)

        # Verifica se estamos na página de detalhes do processo no e-SAJ
        if "Não existem informações disponíveis" in driver.page_source or "numeroDigitoAnoUnificado" in driver.page_source:
            print("[*] Não encontrado na busca padrão e-SAJ. Tentando modo 'Outros'...")
            driver.get(url)
            time.sleep(5)
            try:
                radio_outros = wait.until(EC.element_to_be_clickable((By.ID, "radioOutros")))
                radio_outros.click()
                time.sleep(2)
                campo_outros = driver.find_element(By.ID, "dadosConsulta.valorConsulta")
                campo_outros.clear()
                campo_outros.send_keys(numero_cnj)
                time.sleep(5)
                botao = driver.find_element(By.ID, "botaoConsultarProcessos")
                botao.click()
                time.sleep(10)
            except:
                pass

        # Se ainda não estiver na página de detalhes, tenta EPROC
        if "Não existem informações disponíveis" in driver.page_source or "numeroDigitoAnoUnificado" in driver.page_source:
            eproc_res = scrape_eproc(numero_cnj, driver)
            if eproc_res:
                return eproc_res
            else:
                return {"error": "Processo não encontrado nos sistemas TJSP (e-SAJ/EPROC)"}

        # Extração de dados (e-SAJ) - Se chegou aqui, é porque encontrou no e-SAJ
        html = driver.page_source
        soup = BeautifulSoup(html, 'html.parser')
        
        classe = soup.find(id="classeProcesso").get_text().strip() if soup.find(id="classeProcesso") else "N/A"
        assunto = soup.find(id="assuntoProcesso").get_text().strip() if soup.find(id="assuntoProcesso") else "N/A"
        
        movimentacoes = []
        tabela_movs = soup.find(id="tabelaTodasMovimentacoes") or soup.find(id="tabelaUltimasMovimentacoes")
        if tabela_movs:
            linhas = tabela_movs.find_all("tr")
            for linha in linhas[:10]: # Top 10
                tds = linha.find_all("td")
                if len(tds) >= 2:
                    data = tds[0].get_text().strip()
                    descricao = tds[2].get_text().strip() if len(tds) > 2 else tds[1].get_text().strip()
                    movimentacoes.append({"data": data, "descricao": descricao})

        return {
            "numero": numero_cnj,
            "classe": classe,
            "assunto": assunto,
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
