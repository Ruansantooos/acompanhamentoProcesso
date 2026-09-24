# Acompanha — automação de consulta processual (TJSP)

Robô que lê andamentos processuais direto do site do tribunal e os expõe por
HTTP. Serve o produto **Acompanha** (acompanhamento automático de processos):
quando surge uma movimentação nova, o sistema notifica o advogado.

## Como está organizado

| Arquivo | Papel |
|---|---|
| `scraper_tjsp.py` | O robô. Selenium + undetected-chromedriver. Consulta o e-SAJ e, se não achar, cai no EPROC. Saída: JSON no stdout. |
| `scraper_eproc.py` | Robô do EPROC, usado isoladamente. |
| `scraper-tjsp.js` | Ponte Node → Python. Chama o script acima via `execFile` e lê o JSON da última linha. |
| `backend-datajud.js` | Servidor Express (porta 8000) que expõe o robô por HTTP. |

```
HTTP  →  backend-datajud.js  →  scraper-tjsp.js  →  scraper_tjsp.py  →  Chrome  →  TJSP
```

## Rodando

```bash
npm install
pip install -r requirements.txt     # precisa do Google Chrome instalado
node backend-datajud.js             # sobe em http://localhost:8000
```

Consulta:

```bash
curl http://localhost:8000/processos/11830133920248260100
```

O robô também roda sozinho, o que ajuda a depurar sem subir o servidor:

```bash
HEADLESS_MODE=true python scraper_tjsp.py "1183013-39.2024.8.26.0100"
```

Variáveis (arquivo `.env`, veja `.env.example`):

- `HEADLESS_MODE=true` — Chrome sem janela. Sem isso, a janela abre na tela.
- `CHROME_VERSION_MAIN` — força a versão do ChromeDriver. Normalmente
  desnecessário: a versão do Chrome instalado é detectada em tempo de execução.
- `EMAIL_USER` / `EMAIL_PASS` — Gmail + App Password, para as notificações.
- `GEMINI_API_KEY` — resumos por IA.

## Pontos que merecem atenção na revisão

São problemas conhecidos, listados de propósito — não é preciso descobri-los.

1. **O e-SAJ carrega as movimentações por AJAX.** A página do processo vem com
   um `<div id="containerMovimentacoes">` vazio e um botão "Exibir
   movimentações". Sem clicar, a extração volta zerada. Resolvido em
   `expandir_movimentacoes()`, mas é um ponto frágil: qualquer mudança de
   layout do tribunal quebra de novo, e o robô não tem como saber que quebrou.

2. **Só as ~5 movimentações mais recentes.** O e-SAJ carrega o restante por um
   link "Mais", que ainda não é seguido. Suficiente para detectar novidade,
   insuficiente para montar histórico completo.

3. **O EPROC exige captcha.** O clique em "Consultar" dispara o alerta
   *"Aguarde a verificação do captcha"* e a consulta não acontece. Hoje o robô
   detecta isso e devolve erro explícito, em vez de fingir que o processo não
   tem andamento. Processos com numeração nova (só existem no EPROC) não são
   atendidos — é a maior limitação do projeto hoje.

4. **Datas sem hora.** O e-SAJ entrega `dd/mm/aaaa`; não há horário, então duas
   movimentações no mesmo dia não têm ordem garantida entre si.

5. **Sem testes automatizados.** Toda verificação é manual, contra o site real
   do tribunal — que muda sem aviso.

6. **As rotas de monitoramento e cron em `backend-datajud.js` estão
   depreciadas.** Essa parte migrou para a aplicação Next.js (outro
   repositório). O que importa aqui é `GET /processos/:cnj`.

7. **Chave do Firebase no código.** `backend-datajud.js` traz a config do
   Firebase em texto. É uma chave de cliente, pública por natureza, mas a
   proteção real depende das regras do Firestore estarem publicadas — o que
   ainda não foi confirmado.

## Nota importante sobre o que roda em produção

O serviço que está no ar hoje é um worker **FastAPI** (`/api/scan`,
`/api/movimentacoes`), hospedado numa VPS, e **seu código não está neste
repositório nem em nenhum outro** — existe apenas naquela máquina. Se a VPS for
perdida, o código vai junto. Versioná-lo é a pendência mais urgente do projeto.
