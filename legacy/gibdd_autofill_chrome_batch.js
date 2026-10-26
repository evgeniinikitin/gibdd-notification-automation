#!/usr/bin/env node
const fs = require("fs");
const path = require("path");
const { chromium } = require("playwright");
const {
  applyBusinessRules,
  attachDownloadSaver,
  fillSingleNotification,
} = require("./gibdd_autofill_node");

const DEFAULT_CDP = "http://127.0.0.1:9222";
const DEFAULT_DOWNLOAD_DIR = path.join(process.cwd(), "downloads");
const LONG_TIMEOUT = 180000;
const LONG_NAVIGATION_TIMEOUT = 240000;

function readJson(file) {
  return JSON.parse(fs.readFileSync(file, "utf8"));
}

async function main() {
  const args = process.argv.slice(2);
  let cdp = DEFAULT_CDP;
  const payloadPaths = [];
  for (let index = 0; index < args.length; index += 1) {
    const arg = args[index];
    if (arg === "--cdp") {
      cdp = args[index + 1] || DEFAULT_CDP;
      index += 1;
    } else if (arg.startsWith("--cdp=")) {
      cdp = arg.split("=", 2)[1] || DEFAULT_CDP;
    } else {
      payloadPaths.push(arg);
    }
  }

  if (!payloadPaths.length) {
    console.error("Usage: node tools/gibdd_autofill_chrome_batch.js [--cdp http://127.0.0.1:9222] payload1.json ...");
    process.exit(2);
  }

  const browser = await chromium.connectOverCDP(cdp);

  const failures = [];
  for (let index = 0; index < payloadPaths.length; index += 1) {
    const payloadPath = payloadPaths[index];
    const payload = applyBusinessRules(readJson(payloadPath));
    let context;
    try {
      context = await browser.newContext();
    } catch (error) {
      const existing = browser.contexts();
      if (!existing.length) throw error;
      context = existing[0];
    }
    context.setDefaultTimeout(LONG_TIMEOUT);
    context.setDefaultNavigationTimeout(LONG_NAVIGATION_TIMEOUT);
    context.on("page", (p) => attachDownloadSaver(p, DEFAULT_DOWNLOAD_DIR));

    const page = await context.newPage();
    attachDownloadSaver(page, DEFAULT_DOWNLOAD_DIR);
    console.log(`[${index + 1}/${payloadPaths.length}] ${payloadPath}`);
    try {
      await fillSingleNotification(page, payload);
      const readline = require('readline');
      const rl = readline.createInterface({ input: process.stdin, output: process.stdout });
      await new Promise(resolve => {
        rl.question(`\n[ОЖИДАНИЕ] Заявка [${index + 1}/${payloadPaths.length}] заполнена!\nПожалуйста, введите капчу и нажмите 'Отправить' в Google Chrome.\nПосле успешной отправки нажмите ENTER прямо здесь, в терминале, чтобы запустить следующую заявку...`, () => {
          rl.close();
          resolve();
        });
      });
    } catch (error) {
      failures.push({ payloadPath, error });
      console.error(`[${index + 1}/${payloadPaths.length}] Не дошел до финала: ${payloadPath}`);
      console.error(error);
    }
  }

  if (failures.length) {
    console.log(`\nГотовые вкладки оставлены открытыми, но ${failures.length} заявк(и) не дошли до финала:`);
    for (const failure of failures) console.log(`- ${failure.payloadPath}: ${failure.error.message}`);
    process.exitCode = 1;
  } else {
    console.log("\nВсе вкладки доведены до финальной страницы проверки. Капчу/подтверждение оставляю тебе.");
  }

  // Не закрываем браузер: окна с финальными страницами должны остаться открытыми.
  process.exit(process.exitCode || 0);
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
