const puppeteer = require('puppeteer-core');
const chromium = require('@sparticuz/chromium');

/**
 * Robô de consulta TJSP para ambiente serverless (Vercel).
 * Porta a lógica do scraper_tjsp.py (Selenium/undetected-chromedriver) para
 * puppeteer-core + @sparticuz/chromium, já que serverless não roda Python
 * nem instala Chrome via apt.
 *
 * Aviso: o plano Hobby da Vercel mata a função em 10s. Abrir o browser +
 * navegar no e-SAJ (e possivelmente cair no fallback "Outros"/EPROC) pode
 * facilmente ultrapassar isso — por isso o watchdog abaixo devolve um erro
 * claro em vez de deixar a Vercel cortar a resposta sem explicação.
 */
const FUNCTION_TIMEOUT_MS = 8000;

async function withTimeout(promise, ms, label) {
    let timer;
    const timeout = new Promise((_, reject) => {
        timer = setTimeout(() => reject(new Error(`Timeout (${label}) — consulta excedeu ${ms}ms, limite do plano serverless.`)), ms);
    });
    try {
        return await Promise.race([promise, timeout]);
    } finally {
        clearTimeout(timer);
    }
}

async function getBrowser() {
    return puppeteer.launch({
        args: chromium.args,
        defaultViewport: { width: 1920, height: 1080 },
        executablePath: await chromium.executablePath(),
        headless: chromium.headless,
    });
}

async function typeInto(page, elementId, text) {
    const handle = await page.evaluateHandle((id) => document.getElementById(id), elementId);
    const el = handle.asElement();
    if (!el) throw new Error(`Campo #${elementId} não encontrado na página`);
    await el.click({ clickCount: 3 });
    await el.type(text, { delay: 10 });
    return el;
}

async function clickId(page, elementId) {
    const handle = await page.evaluateHandle((id) => document.getElementById(id), elementId);
    const el = handle.asElement();
    if (!el) throw new Error(`Elemento #${elementId} não encontrado na página`);
    await el.click();
}

async function pageContains(page, text) {
    return page.evaluate((t) => document.documentElement.innerHTML.includes(t), text);
}

async function extrairMovimentacoesESaj(page) {
    return page.evaluate(() => {
        const classe = document.getElementById('classeProcesso')?.textContent.trim() || 'N/A';
        const assunto = document.getElementById('assuntoProcesso')?.textContent.trim() || 'N/A';

        const tabela = document.getElementById('tabelaTodasMovimentacoes') || document.getElementById('tabelaUltimasMovimentacoes');
        const movimentacoes = [];
        if (tabela) {
            const linhas = Array.from(tabela.querySelectorAll('tr')).slice(0, 15);
            for (const linha of linhas) {
                const tds = linha.querySelectorAll('td');
                if (tds.length >= 2) {
                    const data = tds[0].textContent.trim();
                    const desc = (tds.length > 2 ? tds[2].textContent : tds[1].textContent).trim().replace(/\s+/g, ' ');
                    movimentacoes.push({ dataHora: data, nome: desc });
                }
            }
        }
        return { classe, assunto, movimentacoes };
    });
}

async function scrapeEproc(page, numeroCnj) {
    console.log('[ROBÔ-JS] Tentando fallback para o sistema EPROC...');
    await page.goto('https://eproc-consulta.tjsp.jus.br/consulta_1g/externo_controlador.php?acao=tjsp@consulta_unificada_publica/consultar', { waitUntil: 'domcontentloaded' });

    let campoId = null;
    for (const id of ['txtNumProcesso', 'txtNumeroProcesso', 'txtProcesso']) {
        if (await page.$(`#${id}`)) { campoId = id; break; }
    }
    if (!campoId) throw new Error('Campo de número do processo não encontrado no EPROC');

    await typeInto(page, campoId, numeroCnj);

    let botaoId = null;
    for (const id of ['btnConsultar', 'btnPesquisar']) {
        if (await page.$(`#${id}`)) { botaoId = id; break; }
    }
    if (botaoId) {
        await clickId(page, botaoId);
    } else {
        await page.click("button[type='submit']");
    }

    await page.waitForFunction(
        () => document.getElementById('tabelaEventos') || document.documentElement.innerHTML.includes('Nenhum registro encontrado') || document.documentElement.innerHTML.includes('Resultado da Consulta'),
        { timeout: 6000 }
    ).catch(() => {});

    if (await pageContains(page, 'Nenhum registro encontrado')) {
        return null;
    }

    return page.evaluate((cnj) => {
        let classe = 'N/A', assunto = 'N/A';
        for (const label of document.querySelectorAll('label, th')) {
            const txt = label.textContent.trim();
            if (txt.includes('Classe')) {
                const val = label.parentElement?.querySelector('span, td, div');
                if (val) classe = val.textContent.trim();
            } else if (txt.includes('Assunto')) {
                const val = label.parentElement?.querySelector('span, td, div');
                if (val) assunto = val.textContent.trim();
            }
        }

        const movimentacoes = [];
        const tabela = document.getElementById('tabelaEventos');
        if (tabela) {
            const linhas = Array.from(tabela.querySelectorAll('tr')).slice(1, 15);
            for (const linha of linhas) {
                const tds = linha.querySelectorAll('td');
                if (tds.length >= 3) {
                    const data = tds[1].textContent.trim();
                    const desc = tds[2].textContent.trim().replace(/\s+/g, ' ');
                    movimentacoes.push({ dataHora: data, nome: desc });
                }
            }
        }

        return { numero: cnj, classe, assunto, partes: [], movimentacoes, fonte: 'EPROC-TJSP' };
    }, numeroCnj);
}

async function consultarEsaj(numeroCnj) {
    const clean = numeroCnj.replace(/\D/g, '');
    if (clean.length !== 20) throw new Error('CNJ inválido - deve ter 20 dígitos');

    const primeiraParte = `${clean.slice(0, 7)}-${clean.slice(7, 9)}.${clean.slice(9, 13)}`;
    const foro = clean.slice(16, 20);

    const browser = await getBrowser();
    try {
        const page = await browser.newPage();
        page.setDefaultTimeout(6000);
        page.setDefaultNavigationTimeout(6000);

        const url = 'https://esaj.tjsp.jus.br/cpopg/open.do';
        await page.goto(url, { waitUntil: 'domcontentloaded' });

        try {
            await typeInto(page, 'numeroDigitoAnoUnificado', primeiraParte);
            await typeInto(page, 'foroNumeroUnificado', foro);
            await clickId(page, 'botaoConsultarProcessos');
            await page.waitForFunction(
                () => document.getElementById('classeProcesso') || document.documentElement.innerHTML.includes('Não existem informações disponíveis') || document.documentElement.innerHTML.includes('Mensagem'),
                { timeout: 6000 }
            );
        } catch (e) {
            console.log('[ROBÔ-JS] Falha na busca inicial e-SAJ:', e.message);
        }

        const semResultadoInicial = await pageContains(page, 'Não existem informações disponíveis') || await page.$('#numeroDigitoAnoUnificado') !== null;

        if (semResultadoInicial) {
            console.log('[ROBÔ-JS] Não encontrado na busca padrão e-SAJ. Tentando via CNJ completo...');
            await page.goto(url, { waitUntil: 'domcontentloaded' });
            try {
                await clickId(page, 'radioOutros');
                await typeInto(page, 'dadosConsulta.valorConsulta', numeroCnj);
                await clickId(page, 'botaoConsultarProcessos');
                await page.waitForFunction(
                    () => document.getElementById('classeProcesso') || document.documentElement.innerHTML.includes('Não existem informações disponíveis'),
                    { timeout: 6000 }
                );
            } catch (_) { /* segue pro fallback EPROC */ }
        }

        const aindaSemResultado = await pageContains(page, 'Não existem informações disponíveis') || await page.$('#numeroDigitoAnoUnificado') !== null;

        if (aindaSemResultado) {
            const eprocRes = await scrapeEproc(page, numeroCnj);
            if (eprocRes) return eprocRes;
            return { error: 'Processo não encontrado nos sistemas TJSP (e-SAJ/EPROC)' };
        }

        const { classe, assunto, movimentacoes } = await extrairMovimentacoesESaj(page);
        return {
            numero: numeroCnj,
            classe,
            assunto,
            partes: [],
            movimentacoes,
            fonte: 'e-SAJ-TJSP',
        };
    } finally {
        await browser.close();
    }
}

async function scrapeTJSP(cnj) {
    const cleanCNJ = cnj.replace(/\D/g, '');
    if (cleanCNJ.length !== 20) throw new Error('CNJ inválido');

    const formatted = `${cleanCNJ.substring(0, 7)}-${cleanCNJ.substring(7, 9)}.${cleanCNJ.substring(9, 13)}.${cleanCNJ.substring(13, 14)}.${cleanCNJ.substring(14, 16)}.${cleanCNJ.substring(16, 20)}`;

    console.log(`[ROBÔ-JS] Executando scraper serverless para ${formatted}...`);
    const resultado = await withTimeout(consultarEsaj(formatted), FUNCTION_TIMEOUT_MS, 'scrapeTJSP');

    if (resultado.error) {
        throw new Error(resultado.error);
    }
    if (!resultado.movimentacoes || resultado.movimentacoes.length === 0) {
        throw new Error('Nenhuma movimentação encontrada pelo robô');
    }

    console.log(`[ROBÔ-JS] Sucesso! ${resultado.movimentacoes.length} movimentações extraídas.`);
    return resultado;
}

module.exports = { scrapeTJSP };
