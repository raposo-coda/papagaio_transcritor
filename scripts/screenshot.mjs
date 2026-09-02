/**
 * screenshot.mjs - Tira prints da interface para conferencia visual.
 *
 * Fala CDP direto com um Edge/Chrome headless ja rodando com
 * --remote-debugging-port. Usa o WebSocket embutido do Node (>=22), entao nao
 * precisa de puppeteer nem de nenhuma dependencia instalada.
 *
 * Uso:
 *   node scripts/screenshot.mjs <porta-cdp> <url> <arquivo.png> [--js "codigo"]
 */

import { readFileSync, writeFileSync } from "node:fs";

const [, , portaArg, url, saida, ...resto] = process.argv;
const porta = Number(portaArg || 9222);

let jsExtra = null;
const idxJs = resto.indexOf("--js");
if (idxJs >= 0) jsExtra = resto[idxJs + 1];

// Passar JavaScript como argumento pelo PowerShell corrompe as aspas duplas
// e o script chega invalido. Com --js-file o conteudo nunca passa pela linha
// de comando.
const idxArq = resto.indexOf("--js-file");
if (idxArq >= 0) jsExtra = readFileSync(resto[idxArq + 1], "utf8");

const espera = (ms) => new Promise((r) => setTimeout(r, ms));

async function alvoDaPagina() {
  // O Edge leva um instante para abrir a porta de depuracao.
  for (let i = 0; i < 40; i += 1) {
    try {
      const res = await fetch(`http://127.0.0.1:${porta}/json/list`);
      const alvos = await res.json();
      const pagina = alvos.find((a) => a.type === "page");
      if (pagina) return pagina;
    } catch {
      /* ainda subindo */
    }
    await espera(500);
  }
  throw new Error("nao achei uma pagina no navegador de depuracao");
}

const pagina = await alvoDaPagina();
const ws = new WebSocket(pagina.webSocketDebuggerUrl);
await new Promise((r) => (ws.onopen = r));

let proximoId = 0;
const pendentes = new Map();
ws.onmessage = (evento) => {
  const msg = JSON.parse(evento.data);
  if (msg.id && pendentes.has(msg.id)) {
    const { resolve, reject } = pendentes.get(msg.id);
    pendentes.delete(msg.id);
    msg.error ? reject(new Error(JSON.stringify(msg.error))) : resolve(msg.result);
  }
};

function cdp(method, params = {}) {
  proximoId += 1;
  const id = proximoId;
  return new Promise((resolve, reject) => {
    pendentes.set(id, { resolve, reject });
    ws.send(JSON.stringify({ id, method, params }));
  });
}

await cdp("Page.enable");
await cdp("Runtime.enable");

await cdp("Page.navigate", { url });
await espera(2500);

if (jsExtra) {
  const r = await cdp("Runtime.evaluate", { expression: jsExtra, awaitPromise: true });
  // Sem isto, um erro de sintaxe no script injetado passa despercebido: o CDP
  // responde com sucesso e devolve o erro dentro de exceptionDetails.
  if (r.exceptionDetails) {
    const d = r.exceptionDetails;
    console.error("erro no script injetado:", d.text, d.exception && d.exception.description);
    process.exit(1);
  }
  await espera(2500);
}

const { data } = await cdp("Page.captureScreenshot", { format: "png" });
writeFileSync(saida, Buffer.from(data, "base64"));
console.log(`print salvo: ${saida}`);
ws.close();
process.exit(0);
