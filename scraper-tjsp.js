const { execFile } = require('child_process');
const path = require('path');

/**
 * Robô de consulta TJSP usando Python (undetected-chromedriver).
 * Chama o script scraper_tjsp.py e retorna o resultado como JSON.
 * Essa abordagem contorna proteções anti-bot muito melhor que o Puppeteer puro.
 */
async function scrapeTJSP(cnj) {
    const cleanCNJ = cnj.replace(/\D/g, '');
    if (cleanCNJ.length !== 20) throw new Error('CNJ inválido');

    // Formata o CNJ para o padrão visual: NNNNNNN-DD.AAAA.J.TR.OOOO
    const formatted = `${cleanCNJ.substring(0,7)}-${cleanCNJ.substring(7,9)}.${cleanCNJ.substring(9,13)}.${cleanCNJ.substring(13,14)}.${cleanCNJ.substring(14,16)}.${cleanCNJ.substring(16,20)}`;

    const scriptPath = path.join(__dirname, 'scraper_tjsp.py');

    return new Promise((resolve, reject) => {
        console.log(`[ROBÔ-PY] Executando scraper Python para ${formatted}...`);

        const proc = execFile('python', ['-u', scriptPath, formatted], {
            timeout: 90000, // 90 segundos de timeout
            maxBuffer: 1024 * 1024 * 5, // 5MB de buffer
            cwd: __dirname,
            encoding: 'utf8',
            env: { ...process.env, PYTHONIOENCODING: 'utf-8', PYTHONUTF8: '1' }
        }, (error, stdout, stderr) => {
            if (stderr) {
                console.warn(`[ROBÔ-PY] stderr: ${stderr.substring(0, 300)}`);
            }

            if (error) {
                console.error(`[ROBÔ-PY] Erro ao executar Python:`, error.message);
                return reject(new Error(`Falha no robô Python: ${error.message}`));
            }

            try {
                // O script Python imprime JSON na última linha do stdout
                const lines = stdout.trim().split('\n');
                const jsonLine = lines[lines.length - 1];
                const result = JSON.parse(jsonLine);

                if (result.error) {
                    return reject(new Error(result.error));
                }

                // Valida que temos dados
                if (!result.movimentacoes || result.movimentacoes.length === 0) {
                    return reject(new Error('Nenhuma movimentação encontrada pelo robô Python'));
                }

                console.log(`[ROBÔ-PY] Sucesso! ${result.movimentacoes.length} movimentações extraídas.`);
                resolve(result);
            } catch (parseError) {
                console.error(`[ROBÔ-PY] Erro ao parsear JSON:`, parseError.message);
                console.error(`[ROBÔ-PY] stdout recebido:`, stdout.substring(0, 500));
                reject(new Error('Resposta inválida do robô Python'));
            }
        });
    });
}

module.exports = { scrapeTJSP };
