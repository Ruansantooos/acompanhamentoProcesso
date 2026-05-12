"""
Robô de Consulta Processual - TJSP (e-SAJ)
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


def consultar_esaj(numero_cnj):
    """
    Consulta um processo no e-SAJ do TJSP usando o número CNJ.
    Retorna um dicionário com classe, partes e movimentações.
    """
    # Limpa o CNJ para extrair as partes
    clean = numero_cnj.replace("-", "").replace(".", "")
    if len(clean) != 20:
        return {"error": "CNJ inválido - deve ter 20 dígitos"}

    # Monta as partes do número para os campos do e-SAJ
    # Formato: NNNNNNN-DD.AAAA.J.TR.OOOO
    primeira_parte = f"{clean[0:7]}-{clean[7:9]}.{clean[9:13]}"
    foro = clean[16:20]

    options = uc.ChromeOptions()
    options.add_argument('--headless=new')
    options.add_argument('--no-sandbox')
    options.add_argument('--disable-setuid-sandbox')
    options.add_argument('--disable-gpu')
    options.add_argument('--disable-dev-shm-usage')
    options.add_argument('--window-size=1920,1080')

    driver = None
    try:
        driver = uc.Chrome(options=options, version_main=None)
        wait = WebDriverWait(driver, 20)

        # ─── ROTA 1: e-SAJ CPOPG (Consulta de 1º Grau) ───
        url = "https://esaj.tjsp.jus.br/cpopg/open.do"
        driver.get(url)
        time.sleep(7)

        # Preenche os campos
        campo_numero = wait.until(EC.presence_of_element_located((By.ID, "numeroDigitoAnoUnificado")))
        campo_numero.clear()
        campo_numero.send_keys(primeira_parte)

        campo_foro = driver.find_element(By.ID, "foroNumeroUnificado")
        campo_foro.clear()
        campo_foro.send_keys(foro)

        # Espera 5 segundos após preencher (conforme sugerido pelo usuário para o CAPTCHA)
        print("[*] Aguardando 5s para o CAPTCHA/Validação...")
        time.sleep(5)

        # Clica em Consultar
        botao = driver.find_element(By.ID, "botaoConsultarProcessos")
        botao.click()

        # Aguarda carregamento da página de resultado
        time.sleep(10)

        # Se não carregou o processo (ainda na tela de busca ou com erro), tenta o método "Outros"
        if "Não existem informações disponíveis" in driver.page_source or "numeroDigitoAnoUnificado" in driver.page_source:
            try:
                print("[*] Processo não encontrado na busca padrão. Tentando via campo 'Outros'...")
                # Recarrega a página de busca para garantir
                driver.get("https://esaj.tjsp.jus.br/cpopg/open.do")
                time.sleep(5)
                
                # Seleciona o rádio "Outros"
                radio_outros = wait.until(EC.element_to_be_clickable((By.ID, "radioOutros")))
                radio_outros.click()
                time.sleep(2)
                
                # Preenche o campo único com o CNJ completo
                campo_outros = driver.find_element(By.ID, "dadosConsulta.valorConsulta")
                campo_outros.clear()
                campo_outros.send_keys(numero_cnj)
                
                # Espera 5 segundos após preencher
                print("[*] Aguardando 5s para o CAPTCHA (Modo Outros)...")
                time.sleep(5)
                
                # Clica em Consultar
                botao = driver.find_element(By.ID, "botaoConsultarProcessos")
                botao.click()
                time.sleep(10)
            except Exception as e:
                print(f"Erro ao tentar busca por 'Outros': {e}")

        # Salva screenshot para debug
        try:
            driver.save_screenshot(os.path.join(os.path.dirname(__file__), "debug_esaj.png"))
        except:
            pass

        # Verifica se caiu em tela de erro/captcha
        page_source = driver.page_source

        if "Não existem informações disponíveis" in page_source:
            return {"error": "Processo não encontrado no e-SAJ"}

        # ─── EXTRAÇÃO COM BEAUTIFULSOUP ───
        soup = BeautifulSoup(page_source, 'html.parser')

        # Classe processual - se existe, o processo foi encontrado
        classe_el = soup.select_one('#classeProcesso')
        
        # Se não encontrou a classe, verifica se é segredo de justiça
        if not classe_el:
            if "senhaProcesso" in page_source:
                return {"error": "Processo em Segredo de Justiça (exige senha/login OAB)"}
            return {"error": "Processo não encontrado ou página não carregou corretamente"}

        classe = classe_el.text.strip()

        # Assunto
        assunto_el = soup.select_one('#assuntoProcesso')
        assunto = assunto_el.text.strip() if assunto_el else ""

        # Juiz
        juiz_el = soup.select_one('#juizProcesso')
        juiz = juiz_el.text.strip() if juiz_el else ""

        # Foro / Vara
        foro_el = soup.select_one('#foroProcesso')
        foro_nome = foro_el.text.strip() if foro_el else ""
        vara_el = soup.select_one('#varaProcesso')
        vara_nome = vara_el.text.strip() if vara_el else ""

        # Partes do processo
        partes = []
        tabela_partes = soup.select('#tablePartesPrincipais tr')
        for linha in tabela_partes:
            tipo_el = linha.select_one('.tipoDeParticipacao')
            nome_el = linha.select_one('.nomeParteEAdvogado')
            if tipo_el and nome_el:
                tipo = tipo_el.text.strip().rstrip(':').upper()
                nome = ' '.join(nome_el.text.split())
                partes.append({
                    "polo": tipo if tipo in ["REQTE", "REQDO", "AUTOR", "RÉU", "EXEQTE", "EXECTDO"] else "OUTRO",
                    "nome": nome,
                    "documento": "***"
                })

        # Movimentações - tenta vários seletores do e-SAJ
        movimentacoes = []
        
        # Seletor 1: tabela com tr (padrão antigo)
        linhas_mov = soup.select('#tabelaTodasMovimentacoes tr')
        
        # Seletor 2: containerMovimentacao (padrão novo)
        if not linhas_mov:
            linhas_mov = soup.select('#tabelaTodasMovimentacoes .containerMovimentacao')
        
        # Seletor 3: tabelaUltimasMovimentacoes (fallback)
        if not linhas_mov:
            linhas_mov = soup.select('#tabelaUltimasMovimentacoes tr')

        for linha in linhas_mov:
            data_el = linha.select_one('.dataMovimentacao')
            desc_el = linha.select_one('.descricaoMovimentacao')
            if data_el and desc_el:
                data_texto = data_el.text.strip()
                desc_texto = ' '.join(desc_el.text.split())
                if data_texto:
                    movimentacoes.append({
                        "dataHora": data_texto,
                        "nome": desc_texto
                    })

        resultado = {
            "classe": classe,
            "assunto": assunto,
            "juiz": juiz,
            "foro": foro_nome,
            "vara": vara_nome,
            "tribunal_sigla": "TJSP",
            "partes": partes,
            "movimentacoes": movimentacoes
        }

        return resultado

    except Exception as e:
        return {"error": f"Erro no robô Python: {str(e)}"}
    finally:
        if driver:
            try:
                driver.quit()
            except:
                pass


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(json.dumps({"error": "Uso: python scraper_tjsp.py <numero_cnj>"}))
        sys.exit(1)

    cnj = sys.argv[1]
    resultado = consultar_esaj(cnj)

    # Saída JSON para o Node.js consumir via stdout
    print(json.dumps(resultado, ensure_ascii=False))
