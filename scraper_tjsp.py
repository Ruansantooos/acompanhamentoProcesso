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
        
        # Aguarda a página de resultados carregar (procura pela tabela de eventos ou mensagem de erro)
        try:
            wait.until(lambda d: "tabelaEventos" in d.page_source or "Nenhum registro encontrado" in d.page_source or "Resultado da Consulta" in d.page_source)
        except:
            time.sleep(5) # Fallback
        
        if "Nenhum registro encontrado" in driver.page_source:
            return None
            
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
        chrome_bin = os.environ.get('CHROME_BIN')
        driver = uc.Chrome(options=options, browser_executable_path=chrome_bin) if chrome_bin else uc.Chrome(options=options)
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
        soup = BeautifulSoup(driver.page_source, 'html.parser')
        
        classe = soup.find(id="classeProcesso").get_text().strip() if soup.find(id="classeProcesso") else "N/A"
        assunto = soup.find(id="assuntoProcesso").get_text().strip() if soup.find(id="assuntoProcesso") else "N/A"
        
        movimentacoes = []
        tabela_movs = soup.find(id="tabelaTodasMovimentacoes") or soup.find(id="tabelaUltimasMovimentacoes")
        if tabela_movs:
            for linha in tabela_movs.find_all("tr")[:15]:
                tds = linha.find_all("td")
                if len(tds) >= 2:
                    data = tds[0].get_text().strip()
                    desc = tds[2].get_text().strip() if len(tds) > 2 else tds[1].get_text().strip()
                    desc = " ".join(desc.split())
                    movimentacoes.append({"dataHora": data, "nome": desc})

        return {
            "numero": numero_cnj,
            "classe": classe,
            "assunto": assunto,
            "partes": [], # TODO: Implementar extração de partes no e-SAJ
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
