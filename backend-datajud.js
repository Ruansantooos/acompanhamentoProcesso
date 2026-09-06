require('dotenv').config();
const express = require('express');
const cors = require('cors');
const cron = require('node-cron');
const nodemailer = require('nodemailer');
const { initializeApp } = require("firebase/app");
const { getFirestore, collection, getDocs, setDoc, doc, deleteDoc } = require("firebase/firestore");
// Na Vercel não há Python nem Chrome via apt (ambiente serverless), então usamos
// o robô em puppeteer-core+@sparticuz/chromium; fora da Vercel (Railway/Render/local)
// mantemos o robô Python+Selenium original.
const { scrapeTJSP } = process.env.VERCEL
    ? require('./scraper-tjsp-vercel')
    : require('./scraper-tjsp');

const fs = require('fs');
const path = require('path');
const app = express();
app.use(cors());
app.use(express.json());

// Configuração do Firebase fornecida
const firebaseConfig = {
  apiKey: "AIzaSyB9JWmylJ2Tat5FBZJb_k6VZnsIR85u7CU",
  authDomain: "data-jud.firebaseapp.com",
  projectId: "data-jud",
  storageBucket: "data-jud.firebasestorage.app",
  messagingSenderId: "391984636978",
  appId: "1:391984636978:web:c853d393d56a37024e082b",
  measurementId: "G-XVXCYRG7P0"
};

// Inicializando Firebase
const firebaseApp = initializeApp(firebaseConfig);
const db = getFirestore(firebaseApp);

// Cache em memória para consultas recentes (evita sobrecarga no robô)
const consultaCache = new Map();
const CACHE_TTL = 10 * 60 * 1000; // 10 minutos

const LOCAL_DB_PATH = path.join(__dirname, 'monitoramentos.json');

async function loadMonitoramentos() {
    let monitoramentos = [];
    
    // Tenta carregar do Firestore
    try {
        const querySnapshot = await getDocs(collection(db, "monitoramentos"));
        querySnapshot.forEach((doc) => {
            monitoramentos.push(doc.data());
        });
        console.log(`[FIREBASE] ${monitoramentos.length} monitoramentos carregados.`);
    } catch (e) {
        console.warn("[FIREBASE] Erro ao carregar:", e.message);
        console.warn("[FIREBASE] Usando fallback local para monitoramentos.");
    }

    // Fallback/Merge com arquivo local
    if (fs.existsSync(LOCAL_DB_PATH)) {
        try {
            const localData = JSON.parse(fs.readFileSync(LOCAL_DB_PATH, 'utf8'));
            console.log(`[LOCAL DB] ${localData.length} registros encontrados.`);
            
            // Sincronizar local -> Firebase se o Firebase estiver vazio
            if (monitoramentos.length === 0 && localData.length > 0) {
                console.log("[SYNC] Sincronizando dados locais para o Firebase...");
                for (const m of localData) {
                    await setDoc(doc(db, "monitoramentos", m.id), m);
                    monitoramentos.push(m);
                }
                console.log("[SYNC] Sincronização concluída.");
            } else if (localData.length > 0) {
                // Merge simples (apenas IDs novos)
                localData.forEach(lm => {
                    if (!monitoramentos.find(m => m.id === lm.id)) {
                        monitoramentos.push(lm);
                    }
                });
            }
        } catch (e) {
            console.error("[LOCAL DB] Erro ao ler arquivo:", e.message);
        }
    }

    console.log(`[TOTAL] ${monitoramentos.length} monitoramentos prontos para uso.`);
    return monitoramentos;
}

async function saveMonitoramentos(dataArray) {
    // Salva no Firestore
    try {
        for (const item of dataArray) {
            await setDoc(doc(db, "monitoramentos", item.id), item);
        }
    } catch (e) {
        console.warn("[FIREBASE] Falha ao salvar remotamente, salvando apenas local.");
    }

    // Salva no arquivo local sempre
    try {
        let current = [];
        if (fs.existsSync(LOCAL_DB_PATH)) {
            current = JSON.parse(fs.readFileSync(LOCAL_DB_PATH, 'utf-8'));
        }
        
        dataArray.forEach(newItem => {
            const idx = current.findIndex(c => c.id === newItem.id);
            if (idx !== -1) current[idx] = newItem;
            else current.push(newItem);
        });

        fs.writeFileSync(LOCAL_DB_PATH, JSON.stringify(current, null, 2));
    } catch (e) {
        console.error("[LOCAL DB] Erro ao salvar:", e.message);
    }
}

// Configuração do e-mail (Gmail via App Password)
function createTransporter() {
    return nodemailer.createTransport({
        service: 'gmail',
        auth: {
            user: process.env.EMAIL_USER,
            pass: process.env.EMAIL_PASS,
        }
    });
}

function getTribunalFromCNJ(cnj) {
    const cleanCNJ = cnj.replace(/\D/g, '');
    if (cleanCNJ.length !== 20) return null;

    const j = cleanCNJ.charAt(13);
    const tr = cleanCNJ.substring(14, 16);

    if (j === '8') {
        const ufs = {
            "01": "tjac", "02": "tjal", "03": "tjap", "04": "tjam", "05": "tjba",
            "06": "tjce", "07": "tjdft", "08": "tjes", "09": "tjgo", "10": "tjma",
            "11": "tjmt", "12": "tjms", "13": "tjmg", "14": "tjpa", "15": "tjpb",
            "16": "tjpr", "17": "tjpe", "18": "tjpi", "19": "tjrj", "20": "tjrn",
            "21": "tjrs", "22": "tjro", "23": "tjrr", "24": "tjsc", "25": "tjse",
            "26": "tjsp", "27": "tjto"
        };
        return ufs[tr] || null;
    } else if (j === '4') {
        return `trf${parseInt(tr)}`;
    } else if (j === '5') {
        return `trt${parseInt(tr)}`;
    }
    return null;
}

// O robô de scraping substitui a necessidade da API pública do Datajud (CNJ)
// que costuma ser instável ou retornar dados parciais.

async function consultarProcesso(cnj) {
    const cleanCNJ = cnj.replace(/\D/g, '');
    const tribunal = getTribunalFromCNJ(cleanCNJ);
    
    if (tribunal !== 'tjsp') {
        throw new Error('No momento, apenas o tribunal TJSP é suportado pelo robô de consulta direta.');
    }

    // Consulta direta via robô (Python + undetected-chromedriver)
    console.log(`[ROBÔ] Iniciando extração direta para ${cleanCNJ}...`);
    const scrapeResult = await scrapeTJSP(cleanCNJ);
    
    return { 
        tribunal_sigla: 'TJSP', 
        movimentacoes: scrapeResult.movimentacoes.map(m => ({ 
            dataHora: m.dataHora, 
            nome: m.nome 
        })), 
        classe: scrapeResult.classe,
        partes: scrapeResult.partes
    };
}

async function enviarEmailNotificacao(destinatario, nomeCliente, cnj, novasMovimentacoes, resumo) {
    if (!process.env.EMAIL_USER || !process.env.EMAIL_PASS) {
        console.warn('[EMAIL] EMAIL_USER ou EMAIL_PASS não configurados. Pulando envio.');
        return;
    }

    const transporter = createTransporter();
    const movHtml = novasMovimentacoes.map(m => {
        const data = new Date(m.dataHora).toLocaleDateString('pt-BR', { day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit' });
        return `<tr>
          <td style="padding:8px 12px;border-bottom:1px solid #2d2d2d;color:#aaa;font-size:13px;white-space:nowrap">${data}</td>
          <td style="padding:8px 12px;border-bottom:1px solid #2d2d2d;color:#e0e0e0;font-size:13px">${m.nome}</td>
        </tr>`;
    }).join('');

    await transporter.sendMail({
        from: `"Golden AI Cockpit" <${process.env.EMAIL_USER}>`,
        to: destinatario,
        subject: `Nova movimentação — ${nomeCliente} | ${cnj}`,
        html: `
        <div style="background:#0a0a0a;font-family:sans-serif;padding:32px;max-width:600px;margin:0 auto;border-radius:12px">
          <div style="display:flex;align-items:center;gap:10px;margin-bottom:24px">
            <span style="color:#10b981;font-size:22px">⚖️</span>
            <span style="color:#fff;font-size:18px;font-weight:700">Golden AI <span style="color:#555;font-weight:400">Cockpit</span></span>
          </div>
          <h2 style="color:#fff;font-size:16px;margin:0 0 4px">⚠️ Prazo / Ação Necessária Identificada</h2>
          <p style="color:#6b7280;font-size:13px;margin:0 0 16px">Processo <code style="background:#1a1a1a;color:#10b981;padding:2px 6px;border-radius:4px">${cnj}</code> — ${nomeCliente}</p>
          
          ${resumo ? `
          <div style="background:#1a1a1a;border-left:4px solid #10b981;padding:16px;margin-bottom:24px;border-radius:4px">
            <h3 style="color:#10b981;font-size:14px;margin:0 0 8px">Resumo da IA</h3>
            <p style="color:#e0e0e0;font-size:13px;line-height:1.5;margin:0">${resumo}</p>
          </div>
          ` : ''}

          <table style="width:100%;border-collapse:collapse;background:#111;border-radius:8px;overflow:hidden">
            <thead>
              <tr>
                <th style="padding:10px 12px;text-align:left;color:#10b981;font-size:11px;text-transform:uppercase;letter-spacing:1px;border-bottom:1px solid #222">Data</th>
                <th style="padding:10px 12px;text-align:left;color:#10b981;font-size:11px;text-transform:uppercase;letter-spacing:1px;border-bottom:1px solid #222">Movimentação</th>
              </tr>
            </thead>
            <tbody>${movHtml}</tbody>
          </table>
          <p style="color:#374151;font-size:11px;margin-top:24px">Enviado automaticamente pelo Golden AI Cockpit.</p>
        </div>`
    });

    console.log(`[EMAIL] Notificação enviada para ${destinatario} — processo ${cnj}`);
}

// Mapa de jobs ativos: id -> objeto cron task
const activeCronJobs = {};

// Converte horário HH:MM e frequência para expressão cron
// frequencia: "diario", "12h", "6h", "3h", "1h"
function buildCronExpression(horario, frequencia) {
    if (!horario && frequencia !== '1h' && frequencia !== '3h' && frequencia !== '6h' && frequencia !== '12h') {
        return null;
    }

    switch (frequencia) {
        case '1h':   return '0 * * * *';
        case '3h':   return '0 */3 * * *';
        case '6h':   return '0 */6 * * *';
        case '12h':  return '0 */12 * * *';
        case 'diario': {
            if (!horario) return null;
            const [hh, mm] = horario.split(':');
            return `${mm} ${hh} * * *`;
        }
        default: return null;
    }
}

function checkPossuiPrazo(movimentacoes) {
    const prazosKeywords = [
        "intimação", "citação", "publicação", "ato ordinatório", 
        "vista", "audiência", "prazo", "certidão de publicação",
        "nota de expediente", "despacho", "decisão", "sentença"
    ];
    
    return movimentacoes.some(mov => {
        const nomeMov = (mov.nome || '').toLowerCase();
        const isConclusao = nomeMov.includes("conclus") || nomeMov.includes("recebidos os autos");
        const isJuntadaComum = nomeMov.includes("juntada de petição") || nomeMov.includes("juntada de documento");
        return !isConclusao && !isJuntadaComum && prazosKeywords.some(keyword => nomeMov.includes(keyword));
    });
}

async function verificarENotificar(monitor) {
    const { id, cnj, email, nomeCliente } = monitor;
    console.log(`[MONITOR] INICIANDO VERIFICAÇÃO: Processo ${cnj} para ${email}...`);

    try {
        const resultado = await consultarProcesso(cnj);
        console.log(`[MONITOR] Robô retornou ${resultado.movimentacoes?.length || 0} movimentações para ${cnj}`);
        const monitoramentos = await loadMonitoramentos();
        const idx = monitoramentos.findIndex(m => m.id === id);
        if (idx === -1) return;

        const ultimaMovConhecida = monitoramentos[idx].ultimaMovimentacaoConhecida;
        const movimentacoes = resultado.movimentacoes;

        if (movimentacoes.length === 0) {
            monitoramentos[idx].ultimaVerificacao = new Date().toISOString();
            await saveMonitoramentos([monitoramentos[idx]]);
            return;
        }

        const maisRecente = movimentacoes[0];

        if (!ultimaMovConhecida) {
            // Primeira verificação: salvar estado atual sem notificar
            monitoramentos[idx].ultimaMovimentacaoConhecida = maisRecente.dataHora;
            monitoramentos[idx].ultimaVerificacao = new Date().toISOString();
            await saveMonitoramentos([monitoramentos[idx]]);
            console.log(`[MONITOR] Estado inicial registrado para ${cnj}`);
            return;
        }

        // Filtrar movimentações mais recentes que a última conhecida
        const novas = movimentacoes.filter(m => new Date(m.dataHora) > new Date(ultimaMovConhecida));

        if (novas.length > 0) {
            // Verifica se alguma das novas movimentações indica prazo/ação necessária
            const temPrazo = checkPossuiPrazo(novas);

            if (temPrazo) {
                let resumo = "";
                const GEMINI_API_KEY = process.env.GEMINI_API_KEY;
                if (GEMINI_API_KEY && GEMINI_API_KEY !== "COLOQUE_SUA_CHAVE_AQUI") {
                    try {
                        const { GoogleGenerativeAI } = require('@google/generative-ai');
                        const genAI = new GoogleGenerativeAI(GEMINI_API_KEY);
                        const model = genAI.getGenerativeModel({ model: 'gemini-1.5-flash' });
                        
                        const ultimasMovs = movimentacoes.slice(0, 5).map(m => `- ${m.data}: ${m.descricao}`).join("\n");
                        const prompt = `Você é um assistente jurídico avançado. O processo teve movimentações recentes que indicam abertura de prazo ou ação necessária.
Escreva um resumo direto (máximo de 2 frases) explicando a situação atual do processo e o que deve ser feito.
Movimentações recentes:\n${ultimasMovs}`;
                        
                        const result = await model.generateContent(prompt);
                        resumo = result.response.text();
                    } catch (e) {
                        console.error("[GEMINI] Erro ao gerar resumo:", e.message);
                    }
                }

                await enviarEmailNotificacao(email, nomeCliente, cnj, novas, resumo);
                monitoramentos[idx].ultimaNotificacao = new Date().toISOString();
                console.log(`[MONITOR] Prazo detectado e notificado em ${cnj}`);
            } else {
                console.log(`[MONITOR] Novas movs em ${cnj}, mas nenhum prazo aparente.`);
            }

            monitoramentos[idx].ultimaMovimentacaoConhecida = maisRecente.dataHora;
            monitoramentos[idx].ultimaVerificacao = new Date().toISOString();
            await saveMonitoramentos([monitoramentos[idx]]);
        } else {
            monitoramentos[idx].ultimaVerificacao = new Date().toISOString();
            await saveMonitoramentos([monitoramentos[idx]]);
            console.log(`[MONITOR] Sem novidades em ${cnj}`);
        }
    } catch (err) {
        console.error(`[MONITOR] Erro ao verificar ${cnj}:`, err.message);
    }
}

function iniciarJob(monitor) {
    // Em serverless (Vercel) cada invocação sobe e morre — um cron em memória
    // nunca chega a disparar. As verificações passam a ser só sob demanda
    // (rota /api/monitoramento/:id/verificar).
    if (process.env.VERCEL) return;

    const expr = buildCronExpression(monitor.horario, monitor.frequencia);
    if (!expr || !cron.validate(expr)) {
        console.warn(`[CRON] Expressão inválida para monitor ${monitor.id}: "${expr}"`);
        return;
    }

    if (activeCronJobs[monitor.id]) {
        activeCronJobs[monitor.id].stop();
    }

    const task = cron.schedule(expr, () => verificarENotificar(monitor), {
        timezone: 'America/Sao_Paulo'
    });

    activeCronJobs[monitor.id] = task;
    console.log(`[CRON] Job iniciado para ${monitor.cnj} — "${expr}" (${monitor.frequencia})`);
}

function pararJob(id) {
    if (activeCronJobs[id]) {
        activeCronJobs[id].stop();
        delete activeCronJobs[id];
    }
}

// Inicializar jobs ao subir o servidor
async function inicializarJobs() {
    const monitoramentos = await loadMonitoramentos();
    monitoramentos.filter(m => m.ativo).forEach(iniciarJob);
    console.log(`[CRON] ${monitoramentos.filter(m => m.ativo).length} job(s) iniciado(s).`);
}

// ─── ROTAS ───────────────────────────────────────────────────────────────────

app.get('/processos/:cnj', async (req, res) => {
    const cnj = req.params.cnj;
    const cleanCNJ = cnj.replace(/\D/g, '');

    // Verificar Cache
    const now = Date.now();
    if (consultaCache.has(cleanCNJ)) {
        const cached = consultaCache.get(cleanCNJ);
        if (now - cached.timestamp < CACHE_TTL) {
            console.log(`[CACHE] Retornando resultado em cache para ${cleanCNJ}`);
            return res.json(cached.data);
        }
    }

    try {
        const resultado = await consultarProcesso(cleanCNJ);
        
        const formattedResponse = {
            classe: resultado.classe || 'Não informada',
            tribunal_sigla: resultado.tribunal_sigla,
            movimentacoes: resultado.movimentacoes,
            partes: resultado.partes || [],
            resumo: "Resumo não gerado."
        };

        // Gerar resumo com IA se disponível
        await preencherResumoGemini(formattedResponse, cleanCNJ);

        // Salvar no Cache
        consultaCache.set(cleanCNJ, { timestamp: Date.now(), data: formattedResponse });
        
        return res.json(formattedResponse);
    } catch (error) {
        console.error(`[ROBÔ] Erro na consulta (${cleanCNJ}):`, error.message);
        return res.status(500).json({ 
            error: 'Erro ao consultar o Tribunal: ' + error.message,
            source: 'Robot-Scraper'
        });
    }
});

async function preencherResumoGemini(formattedResponse, cnj) {
    const GEMINI_API_KEY = process.env.GEMINI_API_KEY;
    if (GEMINI_API_KEY && GEMINI_API_KEY !== "COLOQUE_SUA_CHAVE_AQUI" && formattedResponse.movimentacoes.length > 0) {
        try {
            console.log(`[GEMINI] Gerando resumo para o processo ${cnj}...`);
            const { GoogleGenerativeAI } = require('@google/generative-ai');
            const genAI = new GoogleGenerativeAI(GEMINI_API_KEY);
            const model = genAI.getGenerativeModel({ model: 'gemini-2.0-flash' });
            
            const ultimasMovs = formattedResponse.movimentacoes.slice(0, 5).map(m => `- ${m.dataHora}: ${m.nome}`).join("\n");
            const prompt = `Você é um assistente jurídico avançado. Abaixo estão as últimas 5 movimentações de um processo judicial.
Escreva um breve resumo (máximo de 2 frases) explicando de forma clara e direta em que fase o processo está no momento e o que aconteceu por último.
Movimentações:\n${ultimasMovs}`;
            
            const result = await model.generateContent(prompt);
            formattedResponse.resumo = result.response.text();
        } catch (aiError) {
            console.error("[GEMINI] Erro:", aiError.message);
            formattedResponse.resumo = "Erro ao gerar o resumo inteligente.";
        }
    }
}

// Listar todos os monitoramentos
app.get('/api/monitoramento', async (req, res) => {
    const monitoramentos = await loadMonitoramentos();
    // Incluir status do job
    const result = monitoramentos.map(m => ({
        ...m,
        jobAtivo: !!activeCronJobs[m.id]
    }));
    res.json(result);
});

// Criar novo monitoramento
app.post('/api/monitoramento', async (req, res) => {
    const { cnj, email, nomeCliente, frequencia, horario } = req.body;

    if (!cnj || !email || !frequencia) {
        return res.status(400).json({ error: 'cnj, email e frequencia são obrigatórios' });
    }

    const expr = buildCronExpression(horario, frequencia);
    if (!expr) {
        return res.status(400).json({ error: 'frequencia ou horario inválidos' });
    }

    const monitoramentos = await loadMonitoramentos();
    const existente = monitoramentos.find(m => m.cnj === cnj.replace(/\D/g, '') && m.email === email);
    if (existente) {
        return res.status(409).json({ error: 'Já existe um monitoramento para este processo e e-mail' });
    }

    const monitor = {
        id: Date.now().toString(36) + Math.random().toString(36).slice(2, 6),
        cnj: cnj.replace(/\D/g, ''),
        email,
        nomeCliente: nomeCliente || cnj,
        frequencia,
        horario: horario || null,
        ativo: true,
        criadoEm: new Date().toISOString(),
        ultimaVerificacao: null,
        ultimaNotificacao: null,
        ultimaMovimentacaoConhecida: null
    };

    await setDoc(doc(db, "monitoramentos", monitor.id), monitor);
    iniciarJob(monitor);

    res.status(201).json(monitor);
});

// Alternar status ativo/inativo
app.post('/api/monitoramento/:id/toggle', async (req, res) => {
    const monitoramentos = await loadMonitoramentos();
    const idx = monitoramentos.findIndex(m => m.id === req.params.id);
    if (idx === -1) return res.status(404).json({ error: 'Monitoramento não encontrado' });

    monitoramentos[idx].ativo = !monitoramentos[idx].ativo;
    await setDoc(doc(db, "monitoramentos", monitoramentos[idx].id), monitoramentos[idx], { merge: true });

    if (monitoramentos[idx].ativo) {
        iniciarJob(monitoramentos[idx]);
    } else {
        pararJob(req.params.id);
    }

    res.json(monitoramentos[idx]);
});

// Verificar agora (manual)
app.post('/api/monitoramento/:id/verificar', async (req, res) => {
    const monitoramentos = await loadMonitoramentos();
    const monitor = monitoramentos.find(m => m.id === req.params.id);
    if (!monitor) return res.status(404).json({ error: 'Monitoramento não encontrado' });

    await verificarENotificar(monitor);
    const atualizado = await loadMonitoramentos();
    res.json(atualizado.find(m => m.id === req.params.id));
});

// Remover monitoramento
app.delete('/monitoramentos/:id', async (req, res) => {
    pararJob(req.params.id);
    await deleteDoc(doc(db, "monitoramentos", req.params.id));
    res.json({ ok: true });
});

// Endpoint de teste de notificação
app.get('/api/test-notification', async (req, res) => {
    try {
        console.log("[TEST] Iniciando teste de e-mail...");
        const mockNovas = [
            { dataHora: new Date().toISOString(), nome: "MOVIMENTAÇÃO DE TESTE: Intimação de Despacho Proferido" },
            { dataHora: new Date().toISOString(), nome: "MOVIMENTAÇÃO DE TESTE: Decisão Publicada no Diário Oficial" }
        ];
        
        let resumo = "Este é um resumo de teste gerado para validar a integração com o Gmail e a IA Gemini.";
        const GEMINI_API_KEY = process.env.GEMINI_API_KEY;
        if (GEMINI_API_KEY && GEMINI_API_KEY !== "COLOQUE_SUA_CHAVE_AQUI") {
            try {
                const { GoogleGenerativeAI } = require('@google/generative-ai');
                const genAI = new GoogleGenerativeAI(GEMINI_API_KEY);
                const model = genAI.getGenerativeModel({ model: 'gemini-1.5-flash' });
                const prompt = "Gere um resumo curto de teste para um processo que recebeu uma intimação fictícia.";
                const result = await model.generateContent(prompt);
                resumo = result.response.text();
            } catch (e) {
                console.warn("[GEMINI] Erro no teste, usando resumo padrão:", e.message);
            }
        }

        await enviarEmailNotificacao(
            process.env.EMAIL_USER, 
            "Usuário de Teste", 
            "0000000-00.0000.0.00.0000", 
            mockNovas, 
            resumo
        );
        
        res.json({ success: true, message: "E-mail de teste enviado para " + process.env.EMAIL_USER });
    } catch (err) {
        console.error("[TEST] Erro no teste de e-mail:", err.message);
        res.status(500).json({ success: false, error: err.message });
    }
});

// Na Vercel a Express app é exportada e chamada pelo runtime serverless
// (ver api/index.js) — não deve fazer app.listen() nem manter cron em memória.
if (!process.env.VERCEL) {
    const PORT = process.env.PORT || 8000;
    app.listen(PORT, () => {
        console.log(`Servidor rodando em http://localhost:${PORT}`);
        console.log(`Configure EMAIL_USER e EMAIL_PASS para envio de notificações.`);
        inicializarJobs();
    });
}

module.exports = app;
