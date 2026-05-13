import undetected_chromedriver as uc
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import time
import json
import sys

def scrape_eproc(numero_cnj):
    options = uc.ChromeOptions()
    # options.add_argument('--headless') # Headless costuma ser bloqueado por Cloudflare
    
    driver = uc.Chrome(options=options)
    try:
        print(f"[*] Buscando no EPROC: {numero_cnj}")
        driver.get("https://eproc-consulta.tjsp.jus.br/consulta_1g/externo_controlador.php?acao=tjsp@consulta_unificada_publica/consultar")
        
        # Espera carregar
        time.sleep(5)
        
        # Tenta localizar o campo de número do processo
        # O ID costuma ser 'txtNumeroProcesso' ou similar no sistema eproc (baseado no sistema do TRF4)
        try:
            campo = WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.ID, "txtNumeroProcesso")))
        except:
            # Fallback para busca por nome ou outro campo se o ID mudar
            campo = driver.find_element(By.NAME, "txtNumeroProcesso")
            
        campo.clear()
        campo.send_keys(numero_cnj)
        
        print("[*] Aguardando 5s para o CAPTCHA/Cloudflare...")
        time.sleep(5)
        
        # Clica em Consultar
        botao = driver.find_element(By.ID, "btnConsultar")
        botao.click()
        
        # Aguarda resultado
        time.sleep(10)
        
        # Verifica se encontrou
        if "Nenhum registro encontrado" in driver.page_source:
            return {"error": "Processo não encontrado no EPROC"}
            
        # Extração básica (Exemplo de seletores eproc comuns)
        # No EPROC, os detalhes costumam ficar em tabelas ou divs específicas
        # Vamos pegar o HTML para análise posterior se falhar
        html = driver.page_source
        
        # Salva screenshot para debug
        driver.save_screenshot("debug_eproc.png")
        
        # Tenta extrair Classe e Assunto
        try:
            classe = driver.find_element(By.XPATH, "//label[contains(text(), 'Classe')]/following-sibling::span").text
            assunto = driver.find_element(By.XPATH, "//label[contains(text(), 'Assunto')]/following-sibling::span").text
        except:
            classe = "N/A"
            assunto = "N/A"
            
        # Extração de Movimentações
        movimentacoes = []
        try:
            linhas = driver.find_elements(By.CSS_SELECTOR, "table#tabelaEventos tr")
            for linha in linhas[1:11]: # Top 10
                cols = linha.find_elements(By.TAG_NAME, "td")
                if len(cols) >= 3:
                    data = cols[1].text
                    desc = cols[2].text
                    movimentacoes.append({"data": data, "descricao": desc})
        except:
            pass
            
        return {
            "numero": numero_cnj,
            "classe": classe,
            "assunto": assunto,
            "movimentacoes": movimentacoes,
            "fonte": "EPROC-TJSP"
        }
        
    finally:
        driver.quit()

if __name__ == "__main__":
    if len(sys.argv) > 1:
        res = scrape_eproc(sys.argv[1])
        print(json.dumps(res, indent=2))
