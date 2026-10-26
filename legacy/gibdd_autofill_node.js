#!/usr/bin/env node
const fs = require("fs");
const path = require("path");
const readline = require("readline");
const { chromium } = require("playwright");

const DEFAULT_URL = "https://xn--80aebkobnwfcnsfk1e0h.xn--p1ai/transportation";
const COMPANY_TEMPLATES = process.env.GIBDD_CARRIER_TEMPLATES;
const DEFAULT_DOWNLOAD_DIR = path.join(process.cwd(), "downloads");
const DEFAULT_EMAIL = process.env.GIBDD_APPLICANT_EMAIL;
const DEFAULT_GOAL = "Перевозка детей";
const DEFAULT_DRIVER_EXPERIENCE = "более 3х лет";
const SINGLE_BUS_CHILD_LIMIT = 55;
const LONG_TIMEOUT = 180000;
const LONG_NAVIGATION_TIMEOUT = 240000;
const BANNED_DRIVERS = new Set((process.env.GIBDD_EXCLUDED_DRIVERS || "").split(",").map((name) => name.trim()).filter(Boolean));

function readJson(file) {
  return JSON.parse(fs.readFileSync(file, "utf8"));
}

function deepMerge(base, override) {
  const result = { ...base };
  for (const [key, value] of Object.entries(override || {})) {
    if (
      value &&
      typeof value === "object" &&
      !Array.isArray(value) &&
      result[key] &&
      typeof result[key] === "object" &&
      !Array.isArray(result[key])
    ) {
      result[key] = deepMerge(result[key], value);
    } else {
      result[key] = value;
    }
  }
  return result;
}

function applyBusinessRules(payload) {
  const templates = COMPANY_TEMPLATES ? readJson(COMPANY_TEMPLATES) : {};
  if (payload.carrier_key) {
    const key = String(payload.carrier_key).trim().toLowerCase();
    if (!templates[key]) throw new Error(`Неизвестный carrier_key: ${payload.carrier_key}`);
    payload = deepMerge(templates[key], payload);
  }

  payload.applicant = payload.applicant || {};
  payload.applicant.position = "Генеральный директор";
  if (DEFAULT_EMAIL) payload.applicant.email = DEFAULT_EMAIL;

  payload.transportation = payload.transportation || {};
  payload.transportation.goal = DEFAULT_GOAL;

  for (const driver of payload.drivers || []) {
    const driverName = String(driver.name || "").trim();
    if (BANNED_DRIVERS.has(driverName)) {
      throw new Error(`Запрещенный водитель в payload: ${driverName}`);
    }
    driver.experience = DEFAULT_DRIVER_EXPERIENCE;
  }

  if (payload.route && payload.route.roads) {
    payload.route.schedule = payload.route.roads;
  }

  const childrenCount = Number(payload.transportation.children_count || 0);
  const vehicleCount = Array.isArray(payload.vehicles) ? payload.vehicles.length : 0;
  const driverCount = Array.isArray(payload.drivers) ? payload.drivers.length : 0;
  if (childrenCount > SINGLE_BUS_CHILD_LIMIT && (vehicleCount < 2 || driverCount < 2)) {
    throw new Error(
      `Нельзя подавать ${childrenCount} детей одним автобусом: при количестве больше ${SINGLE_BUS_CHILD_LIMIT} нужны минимум 2 автобуса и 2 водителя в payload.`
    );
  }

  for (const stop of payload.route?.stop_points || []) {
    const goal = String(stop.goal || "").toLowerCase();
    if (goal.includes("оконч")) {
      stop.time = "00:15";
      stop.goal = "Окончание поездки";
    } else if (goal.includes("посад") || goal.includes("высад")) {
      stop.time = "00:15";
      stop.goal = "Посадка/Высадка детей";
    } else if (stop.time) {
      stop.time = "02:00";
    }
  }

  return payload;
}

async function waitEnter(message) {
  const rl = readline.createInterface({ input: process.stdin, output: process.stdout });
  await new Promise((resolve) => rl.question(message, resolve));
  rl.close();
}

function safeDownloadName(name) {
  const clean = String(name || "gibdd-download.pdf")
    .replace(/[\\/:*?"<>|]/g, "_")
    .trim();
  if (!clean) return "gibdd-download.pdf";
  return path.extname(clean) ? clean : `${clean}.pdf`;
}

function uniquePath(dir, filename) {
  const parsed = path.parse(filename);
  let candidate = path.join(dir, filename);
  let index = 2;
  while (fs.existsSync(candidate)) {
    candidate = path.join(dir, `${parsed.name} (${index})${parsed.ext}`);
    index += 1;
  }
  return candidate;
}

function attachDownloadSaver(page, downloadDir) {
  page.on("download", async (download) => {
    fs.mkdirSync(downloadDir, { recursive: true });
    const filename = safeDownloadName(download.suggestedFilename());
    const target = uniquePath(downloadDir, filename);
    await download.saveAs(target);
    console.log(`Скачанный файл сохранен: ${target}`);
  });
}

async function fill(page, name, value) {
  if (value === undefined || value === null) return;
  const locator = page.locator(`[name="${name}"]`);
  await locator.waitFor({ state: "attached" });
  await locator.fill(String(value));
}

async function fillIfVisible(page, name, value) {
  if (value === undefined || value === null) return;
  const locator = page.locator(`[name="${name}"]`);
  await locator.waitFor({ state: "attached" });
  if (await locator.isVisible()) await locator.fill(String(value));
}

async function setInputValue(page, name, value) {
  if (value === undefined || value === null) return;
  await page.locator(`[name="${name}"]`).waitFor({ state: "attached" });
  await page.evaluate(
    ([selector, nextValue]) => {
      const input = document.querySelector(selector);
      if (!input) return;
      input.focus();
      input.value = String(nextValue);
      for (const eventName of ["input", "keyup", "change", "blur"]) {
        input.dispatchEvent(new Event(eventName, { bubbles: true }));
      }
    },
    [`[name="${name}"]`, value],
  );
}

async function inputValue(page, name) {
  const locator = page.locator(`[name="${name}"]`);
  await locator.waitFor({ state: "attached" });
  return locator.inputValue();
}

async function selectValue(page, name, value) {
  if (value === undefined || value === null) return;
  const locator = page.locator(`[name="${name}"]`);
  await locator.waitFor({ state: "attached" });
  await page.evaluate(
    ([selector, nextValue]) => {
      const select = document.querySelector(selector);
      if (!select) return;
      select.value = String(nextValue);
      select.dispatchEvent(new Event("input", { bubbles: true }));
      select.dispatchEvent(new Event("change", { bubbles: true }));
      if (window.jQuery) window.jQuery(select).val(String(nextValue)).trigger("change");
    },
    [`[name="${name}"]`, value],
  );
}

async function selectOrAddValue(page, name, value, label) {
  if (value === undefined || value === null) return;
  await page.locator(`[name="${name}"]`).waitFor({ state: "attached" });
  await page.evaluate(
    ([selector, nextValue, nextLabel]) => {
      const select = document.querySelector(selector);
      if (!select) return;
      let option = Array.from(select.options).find((item) => item.value === String(nextValue));
      if (!option) {
        option = new Option(nextLabel || String(nextValue), String(nextValue), true, true);
        select.add(option);
      }
      select.value = String(nextValue);
      select.dispatchEvent(new Event("change", { bubbles: true }));
    },
    [`[name="${name}"]`, value, label],
  );
}

async function fillRegions(page, codes) {
  if (!codes?.length) return;
  const locator = page.locator('select[name="region_code[]"]');
  await locator.waitFor({ state: "attached" });
  await locator.selectOption(codes.map(String));
  await locator.dispatchEvent("change");
}

async function uploadFiles(page, files) {
  const existingFiles = (files || []).filter((file) => file && fs.existsSync(file));
  if (!existingFiles.length) return;
  const input = page.locator("#fileupload-input, input[type='file'][name='file[]']").first();
  await input.waitFor({ state: "attached", timeout: LONG_TIMEOUT });
  for (const file of existingFiles) {
    console.log(`Загружаю файл: ${file}`);
    await input.setInputFiles(file);
    await page.waitForFunction(
      (fileName) => {
        const list = document.querySelector("#fileupload-list");
        const error = document.querySelector("#fileupload-error");
        if (error && getComputedStyle(error).display !== "none" && error.textContent.trim()) {
          throw new Error(error.textContent.trim());
        }
        return list && list.textContent.includes(fileName);
      },
      path.basename(file),
      { timeout: LONG_TIMEOUT },
    ).catch(async () => {
      // Некоторые загрузчики показывают серверное имя вместо исходного.
      await page.waitForTimeout(5000);
      const listText = await page.locator("#fileupload-list").textContent().catch(() => "");
      if (!listText.trim()) throw new Error(`Не удалось подтвердить загрузку файла ${file}`);
    });
  }
}

function customerTypeValue(type) {
  const value = String(type || "").trim().toLowerCase();
  return {
    physical: "1",
    person: "1",
    "физическое лицо": "1",
    legal: "2",
    entity: "2",
    organization: "2",
    "юридическое лицо": "2",
    ip: "3",
    "ип": "3",
  }[value] || value;
}

async function fillApplicant(page, payload) {
  const applicant = payload.applicant || {};
  const transportation = payload.transportation || {};
  await fill(page, "name", applicant.name);
  await fill(page, "position", applicant.position);
  await fill(page, "phone", applicant.phone);
  await fill(page, "email", applicant.email);
  await fillRegions(page, transportation.region_codes || []);
  await fill(page, "count", transportation.children_count);
  await fill(page, "goal", transportation.goal);
  await setInputValue(page, "date_start", transportation.date_start);
  await setInputValue(page, "date_end", transportation.date_end);
  await uploadFiles(page, payload.files || []);
}

function formSelector(action) {
  const normalized = String(action || "");
  const withoutSlash = normalized.endsWith("/") ? normalized.slice(0, -1) : normalized;
  const withSlash = withoutSlash.endsWith("/") ? withoutSlash : `${withoutSlash}/`;
  return `form[action="${withSlash}"], form[action="${withoutSlash}"]`;
}

function formDescendantSelector(action, descendant) {
  const normalized = String(action || "");
  const withoutSlash = normalized.endsWith("/") ? normalized.slice(0, -1) : normalized;
  const withSlash = withoutSlash.endsWith("/") ? withoutSlash : `${withoutSlash}/`;
  return `form[action="${withSlash}"] ${descendant}, form[action="${withoutSlash}"] ${descendant}`;
}

async function fillCustomer(page, payload) {
  const customer = payload.customer || {};
  await page.locator(formSelector("/transportation/customers/")).waitFor({ state: "attached" });
  await selectValue(page, "type", customerTypeValue(customer.type));
  await page.waitForTimeout(300);
  if (customerTypeValue(customer.type) === "2") {
    await setInputValue(page, "entity_name", customer.entity_name);
    await setInputValue(page, "entity_address", customer.entity_address);
    await setInputValue(page, "entity_location", customer.entity_location);
    await setInputValue(page, "phone", customer.phone);
    await setInputValue(page, "email", customer.email);
    await setInputValue(page, "inn", customer.inn);
  } else {
    await fillIfVisible(page, "name", customer.name);
    await fillIfVisible(page, "address", customer.address);
    await fillIfVisible(page, "phone", customer.phone);
    await fillIfVisible(page, "email", customer.email);
    await fillIfVisible(page, "inn", customer.inn);
  }
}

async function fillCarrier(page, payload) {
  const carrier = payload.carrier || {};
  await page.locator(formSelector("/transportation/carriers/")).waitFor({ state: "attached" });
  await selectValue(page, "type", customerTypeValue(carrier.type));
  await page.waitForTimeout(300);
  if (customerTypeValue(carrier.type) === "2") {
    await setInputValue(page, "entity_name", carrier.entity_name);
    await setInputValue(page, "entity_address", carrier.entity_address);
    await setInputValue(page, "entity_location", carrier.entity_location);
    await setInputValue(page, "phone", carrier.phone);
    await setInputValue(page, "email", carrier.email);
    await setInputValue(page, "inn", carrier.inn);
  } else {
    await fillIfVisible(page, "name", carrier.name);
    await fillIfVisible(page, "address", carrier.address);
    await fillIfVisible(page, "phone", carrier.phone);
    await fillIfVisible(page, "email", carrier.email);
  }
}

async function normalizeRepeatingBlocks(page, containerSelector, blockSelector, addButtonSelector, fieldPrefix, count) {
  if (count <= 0) return;
  try {
    await page.locator(`${containerSelector} ${blockSelector}`).first().waitFor({ state: "attached", timeout: LONG_TIMEOUT });
  } catch (_) {
    // Some pages render the first block only after the add button script runs.
  }
  let attempts = 0;
  while ((await page.locator(`${containerSelector} ${blockSelector}`).count()) < count) {
    attempts += 1;
    if (attempts > 10) {
      const currentCount = await page.locator(`${containerSelector} ${blockSelector}`).count();
      throw new Error(`Не удалось создать блоки ${fieldPrefix}: нужно ${count}, сейчас ${currentCount}`);
    }
    await page.locator(addButtonSelector).waitFor({ state: "attached", timeout: LONG_TIMEOUT });
    await page.locator(addButtonSelector).evaluate((button) => {
      button.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true, view: window }));
    });
    await page.waitForTimeout(700);
  }
  await page.evaluate(
    ([containerSelector, blockSelector, fieldPrefix, count]) => {
      const container = document.querySelector(containerSelector);
      if (!container) return;
      let blocks = Array.from(container.querySelectorAll(blockSelector));
      while (blocks.length > count) {
        blocks[blocks.length - 1].remove();
        blocks = Array.from(container.querySelectorAll(blockSelector));
      }
      blocks.forEach((block, index) => {
        block.classList.toggle("m-t25", index > 0);
        const deleteLink = block.querySelector('[class*="delete-"]');
        if (deleteLink) deleteLink.style.display = count > 1 ? "" : "none";
        block.querySelectorAll("[name]").forEach((field) => {
          const name = field.getAttribute("name");
          const match = name && name.match(/^\w+\[\d+\]\[(.+)\]$/);
          if (match) field.setAttribute("name", `${fieldPrefix}[${index}][${match[1]}]`);
        });
      });
    },
    [containerSelector, blockSelector, fieldPrefix, count],
  );
}

async function ensureRepeatingPageReady(page, action, containerSelector, blockSelector, addButtonSelector, label) {
  async function ready() {
    return (await page.locator(`${containerSelector} ${blockSelector}`).count()) > 0
      || (await page.locator(addButtonSelector).count()) > 0;
  }

  await page.locator(formSelector(action)).waitFor({ state: "attached" });
  if (await ready()) return;
  await page.waitForTimeout(3000);
  if (await ready()) return;

  console.log(`Страница ${label} открылась без динамических блоков, перезагружаю шаг`);
  await page.goto(new URL(action, DEFAULT_URL).toString(), { waitUntil: "domcontentloaded", timeout: LONG_NAVIGATION_TIMEOUT });
  await page.locator(formSelector(action)).waitFor({ state: "attached", timeout: LONG_TIMEOUT });
  await page.waitForFunction(
    ([containerSelector, blockSelector, addButtonSelector]) =>
      document.querySelector(`${containerSelector} ${blockSelector}`) || document.querySelector(addButtonSelector),
    [containerSelector, blockSelector, addButtonSelector],
    { timeout: LONG_TIMEOUT },
  );
}

async function fillVehicles(page, payload) {
  const vehicles = payload.vehicles || [];
  await ensureRepeatingPageReady(page, "/transportation/vehicles/", "#vehicles", ".vehicle", "#add_vehicle", "автобусов");
  await normalizeRepeatingBlocks(page, "#vehicles", ".vehicle", "#add_vehicle", "vehicles", vehicles.length);
  for (let i = 0; i < vehicles.length; i++) {
    const block = page.locator("#vehicles .vehicle").nth(i);
    await block.locator('[name$="[brand_and_model]"]').fill(vehicles[i].brand_and_model || "");
    await block.locator('[name$="[number]"]').fill(vehicles[i].number || "");
    await block.locator('[name$="[diagnostic_card_info]"]').fill(vehicles[i].diagnostic_card_info || "");
    const navigator = block.locator('[name$="[navigator_info]"]');
    if (vehicles[i].navigator_info !== false) await navigator.check();
    else await navigator.uncheck();
  }
}

async function fillDrivers(page, payload) {
  const drivers = payload.drivers || [];
  await ensureRepeatingPageReady(page, "/transportation/drivers/", "#drivers", ".driver", "#add_driver", "водителей");
  await normalizeRepeatingBlocks(page, "#drivers", ".driver", "#add_driver", "drivers", drivers.length);
  for (let i = 0; i < drivers.length; i++) {
    const block = page.locator("#drivers .driver").nth(i);
    await block.locator('[name$="[name]"]').fill(drivers[i].name || "");
    await block.locator('[name$="[licence]"]').fill(drivers[i].licence || "");
    await block.locator('[name$="[licence_date]"]').fill(drivers[i].licence_date || "");
    await block.locator('[name$="[experience]"]').fill(drivers[i].experience || "");
  }
}

async function ensureStopPointRows(page, count) {
  await page.locator(formSelector("/transportation/route/")).waitFor({ state: "attached" });
  while ((await page.locator(".stop-point-container .stop-point").count()) > count) {
    await page.locator(".stop-point-container .stop-point .delete").last().click();
    await page.waitForTimeout(300);
  }
  while ((await page.locator(".stop-point-container .stop-point").count()) < count) {
    await page.evaluate(() => {
      const button = document.querySelector("#add_stop_point");
      if (button) button.setAttribute("type", "button");
    });
    await page.locator("#add_stop_point").click();
    await page.waitForTimeout(300);
  }
}

async function openMapForField(page, inputName) {
  await page.waitForFunction(
    () => document.querySelectorAll("button.show-map").length > 0,
    { timeout: LONG_TIMEOUT },
  ).catch(() => {});
  await page.evaluate(() => {
    document.querySelectorAll("button.show-map").forEach((button) => button.setAttribute("type", "button"));
    const addStopPoint = document.querySelector("#add_stop_point");
    if (addStopPoint) addStopPoint.setAttribute("type", "button");
  });
  const stopMatch = inputName.match(/^stop_points\[(\d+)\]\[address\]$/);
  let button;
  if (inputName === "address_start") {
    button = page.locator(formDescendantSelector("/transportation/route/", "button.show-map")).first();
  } else if (stopMatch) {
    button = page.locator(".stop-point-container .stop-point").nth(Number(stopMatch[1])).locator("button.show-map");
  } else {
    button = page.locator(`xpath=//input[@name="${inputName}"]/ancestor::div[contains(@class, "row")][1]//button[contains(@class, "show-map")]`);
  }
  await button.waitFor({ state: "attached", timeout: LONG_TIMEOUT });
  await button.scrollIntoViewIfNeeded();
  await button.click({ force: true }).catch(() => button.evaluate((node) => node.click()));
  try {
    await page.locator(".popup").last().waitFor({ state: "visible", timeout: LONG_TIMEOUT });
    return;
  } catch (_) {
    await page.waitForTimeout(1000);
  }
  await button.evaluate((node) => {
    node.setAttribute("type", "button");
    node.dispatchEvent(new MouseEvent("mousedown", { bubbles: true, cancelable: true, view: window }));
    node.dispatchEvent(new MouseEvent("mouseup", { bubbles: true, cancelable: true, view: window }));
    node.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true, view: window }));
  });
  try {
    await page.locator(".popup").last().waitFor({ state: "visible", timeout: LONG_TIMEOUT });
  } catch (error) {
    const screenshot = `/private/tmp/gibdd_route_map_not_opened_${Date.now()}.png`;
    const html = `/private/tmp/gibdd_route_map_not_opened_${Date.now()}.html`;
    await page.screenshot({ path: screenshot, fullPage: true });
    fs.writeFileSync(html, await page.content(), "utf8");
    throw new Error(`Не удалось открыть карту для поля ${inputName}. Скриншот: ${screenshot}. HTML: ${html}`);
  }
}

function meaningfulQueryTokens(query) {
  return String(query || "")
    .toLowerCase()
    .match(/[а-яa-z0-9]+/gi)
    ?.filter((token) => token.length > 2 && !["дом", "улица", "проспект", "корпус", "литера"].includes(token)) || [];
}

async function pickAddressInMapPopup(page, address, query) {
  const searchQuery = query || address;
  const popup = page.locator(".popup").last();
  const searchInput = popup.locator('input[placeholder="Адрес или объект"]').last();
  await searchInput.waitFor({ state: "visible", timeout: LONG_TIMEOUT });
  await searchInput.evaluate((input, value) => {
    input.focus();
    input.value = value;
    for (const eventName of ["input", "keyup", "change"]) {
      input.dispatchEvent(new Event(eventName, { bubbles: true }));
    }
  }, searchQuery);
  await page.waitForTimeout(300);
  await popup.locator('.ymaps-2-1-79-searchbox-button:has-text("Найти")').last().click({ force: true });
  await page.waitForFunction(
    () => {
      const selected = document.querySelector(".popup .ymaps-2-1-79-islets_serp-item.ymaps-2-1-79-islets__selected")
        || document.querySelector(".popup .ymaps-2-1-79-islets_serp-item");
      return selected && selected.textContent.trim().length > 0;
    },
    { timeout: LONG_TIMEOUT },
  );
  const resultText = await page.evaluate(() => {
    const selected = document.querySelector(".popup .ymaps-2-1-79-islets_serp-item.ymaps-2-1-79-islets__selected")
      || document.querySelector(".popup .ymaps-2-1-79-islets_serp-item");
    return selected ? selected.textContent.trim().toLowerCase() : "";
  });
  const tokens = meaningfulQueryTokens(searchQuery);
  if (tokens.length && !tokens.some((token) => resultText.includes(token))) {
    throw new Error(`Яндекс-карта нашла подозрительный адрес для ${searchQuery}: ${resultText}`);
  }
  await page.evaluate(() => {
    const selected = document.querySelector(".popup .ymaps-2-1-79-islets_serp-item.ymaps-2-1-79-islets__selected")
      || document.querySelector(".popup .ymaps-2-1-79-islets_serp-item");
    if (selected) selected.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true, view: window }));
  });
  await page.waitForTimeout(800);
  const chooseButton = page.locator('.popup .ymaps-2-1-79-float-button:has-text("Выбрать")').last();
  await chooseButton.waitFor({ state: "attached", timeout: LONG_TIMEOUT });
  await chooseButton.click({ force: true, timeout: LONG_TIMEOUT });
  await popup.waitFor({ state: "hidden", timeout: LONG_TIMEOUT }).catch(() => page.waitForTimeout(1000));
}

async function pickRouteAddress(page, inputName, address, query) {
  if (!address) return;
  const input = page.locator(`[name="${inputName}"]`);
  await input.waitFor({ state: "attached" });
  await input.fill(address);
  await input.dispatchEvent("change");
  await openMapForField(page, inputName);
  await pickAddressInMapPopup(page, address, query);
  await page.waitForTimeout(500);
  const actual = (await input.inputValue()).trim();
  if (!actual || actual.toLowerCase().includes("москва") || actual.toLowerCase().includes("красная")) {
    throw new Error(`Карта выбрала неверный адрес для ${inputName}: ${actual}`);
  }
}

async function fillRoute(page, payload) {
  const route = payload.route || {};
  const useMap = process.env.GIBDD_ROUTE_DIRECT === "1" ? false : route.use_map !== false;
  await page.locator(formSelector("/transportation/route/")).waitFor({ state: "attached" });
  if (useMap) {
    await pickRouteAddress(page, "address_start", route.address_start, route.address_start_map_query);
  } else {
    await fill(page, "address_start", route.address_start);
  }
  await ensureStopPointRows(page, route.stop_points?.length || 0);
  for (let i = 0; i < (route.stop_points || []).length; i++) {
    const stop = route.stop_points[i];
    if (useMap) {
      await pickRouteAddress(page, `stop_points[${i}][address]`, stop.address, stop.map_query);
    } else {
      await fill(page, `stop_points[${i}][address]`, stop.address);
    }
    await fill(page, `stop_points[${i}][time]`, stop.time);
    await fill(page, `stop_points[${i}][goal]`, stop.goal);
  }
  await page.waitForTimeout(1200);
  await selectOrAddValue(page, "route_region_code", route.route_region_code, route.route_region_name);
  await page.waitForTimeout(500);
  await selectOrAddValue(page, "district", route.district);
  await page.waitForTimeout(500);
  await fill(page, "roads", route.roads);
  await fill(page, "distance", route.distance);
  await fill(page, "schedule", route.schedule || route.roads);
  await fill(page, "duration", route.duration);

  for (const field of ["address_start", "roads", "distance", "schedule", "duration"]) {
    const value = await inputValue(page, field);
    if (!String(value).trim()) throw new Error(`Маршрут не заполнен: ${field}`);
  }
}

async function clickNext(page, formAction) {
  await page.locator(formDescendantSelector(formAction, "button.u-form__sbt:not(.grey)")).click({
    noWaitAfter: true,
    timeout: LONG_TIMEOUT,
  });
}

async function gotoTransportation(page) {
  for (let attempt = 1; attempt <= 3; attempt += 1) {
    try {
      await page.goto(DEFAULT_URL, { waitUntil: "domcontentloaded", timeout: LONG_NAVIGATION_TIMEOUT });
      await page.locator(formSelector("/transportation/applicant/")).waitFor({
        state: "attached",
        timeout: LONG_TIMEOUT,
      });
      return;
    } catch (error) {
      if (attempt === 3) throw error;
      console.log(`Стартовая страница не загрузилась, повторяю попытку ${attempt + 1}/3`);
      await page.waitForTimeout(3000 * attempt);
    }
  }
}

async function waitForForm(page, action, label) {
  const targetUrl = new URL(action, DEFAULT_URL).toString();
  const selector = formSelector(action);
  for (let attempt = 1; attempt <= 3; attempt += 1) {
    try {
      await page.locator(selector).waitFor({ state: "attached", timeout: LONG_TIMEOUT });
      return;
    } catch (_) {
      if (attempt < 3) {
        console.log(`Страница ${label} открылась без формы, перехожу напрямую (${attempt + 1}/3)`);
        await page.goto(targetUrl, { waitUntil: "domcontentloaded", timeout: LONG_NAVIGATION_TIMEOUT }).catch(() => {});
      }
    }
  }
  try {
    await page.locator(selector).waitFor({ state: "attached", timeout: LONG_TIMEOUT });
  } catch (error) {
    const stamp = Date.now();
    const html = `/private/tmp/gibdd_autofill_node_failed_${stamp}.html`;
    fs.writeFileSync(html, await page.content().catch(() => ""), "utf8");
    let screenshot = "";
    try {
      screenshot = `/private/tmp/gibdd_autofill_node_failed_${stamp}.png`;
      await page.screenshot({ path: screenshot, fullPage: true, timeout: 5000 });
    } catch (_) {
      screenshot = "скриншот не удалось сделать";
    }
    const diagnostics = await page.evaluate(() => Array.from(document.querySelectorAll('.error, .error-message, .red-text, .help-block'))
      .filter((node) => {
        const style = window.getComputedStyle(node);
        return style.display !== 'none' && style.visibility !== 'hidden' && node.textContent.trim();
      })
      .map((node) => node.textContent.trim())
      .join('\n')).catch(() => "");
    throw new Error(`Не удалось перейти на страницу ${label}. URL: ${page.url()}. HTML: ${html}. Скриншот: ${screenshot}. Ошибки: ${diagnostics || "нет видимых ошибок"}`);
  }
}

async function waitForConfirmPage(page) {
  try {
    await page.waitForFunction(
      () => window.location.pathname.includes("/transportation/confirm")
        || Boolean(document.querySelector('h2 a[href="/transportation/route/"]')),
      { timeout: LONG_TIMEOUT },
    );
  } catch (error) {
    const stamp = Date.now();
    const html = `/private/tmp/gibdd_confirm_timeout_${stamp}.html`;
    const screenshot = `/private/tmp/gibdd_confirm_timeout_${stamp}.png`;
    fs.writeFileSync(html, await page.content().catch(() => ""), "utf8");
    await page.screenshot({ path: screenshot, fullPage: true, timeout: 5000 }).catch(() => {});
    const diagnostics = await page.evaluate(() => Array.from(document.querySelectorAll('.error, .error-message, .red-text, .help-block'))
      .filter((node) => {
        const style = window.getComputedStyle(node);
        return style.display !== 'none' && style.visibility !== 'hidden' && node.textContent.trim();
      })
      .map((node) => node.textContent.trim())
      .join('\n')).catch(() => "");
    throw new Error(`Не дошел до финальной страницы. URL: ${page.url()}. HTML: ${html}. Скриншот: ${screenshot}. Ошибки: ${diagnostics || "нет видимых ошибок"}`);
  }
}

async function main() {
  const payloadPath = process.argv[2];
  if (!payloadPath) {
    console.error("Usage: node tools/gibdd_autofill_node.js payload.json [profile]");
    process.exit(2);
  }
  const profile = process.argv[3] || "/private/tmp/gibdd-playwright-profile-node";
  const downloadDir = process.argv[4] || DEFAULT_DOWNLOAD_DIR;
  const payload = applyBusinessRules(readJson(payloadPath));

  const context = await chromium.launchPersistentContext(profile, {
    headless: false,
    slowMo: 80,
    locale: "ru-RU",
    timezoneId: "Europe/Moscow",
    acceptDownloads: true,
    downloadsPath: downloadDir,
  });
  context.setDefaultTimeout(LONG_TIMEOUT);
  context.setDefaultNavigationTimeout(LONG_NAVIGATION_TIMEOUT);
  const page = context.pages()[0] || await context.newPage();
  for (const openPage of context.pages()) attachDownloadSaver(openPage, downloadDir);
  context.on("page", (newPage) => attachDownloadSaver(newPage, downloadDir));
  await gotoTransportation(page);

  await fillApplicant(page, payload);
  await page.locator(".js_continue_btn button.u-form__sbt").click({ noWaitAfter: true, timeout: LONG_TIMEOUT });
  await waitForForm(page, "/transportation/customers/", "заказчика");
  console.log("Перешел на заказчика");

  await fillCustomer(page, payload);
  await clickNext(page, "/transportation/customers/");
  await waitForForm(page, "/transportation/carriers/", "перевозчика");
  console.log("Перешел на перевозчика");

  await fillCarrier(page, payload);
  await clickNext(page, "/transportation/carriers/");
  await waitForForm(page, "/transportation/vehicles/", "автобусов");
  console.log("Перешел на автобусы");

  await fillVehicles(page, payload);
  await clickNext(page, "/transportation/vehicles/");
  await waitForForm(page, "/transportation/drivers/", "водителей");
  console.log("Перешел на водителей");

  await fillDrivers(page, payload);
  await clickNext(page, "/transportation/drivers/");
  await waitForForm(page, "/transportation/route/", "маршрута");
  console.log("Перешел на маршрут");

  await fillRoute(page, payload);
  await page.locator(formDescendantSelector("/transportation/route/", ".js_continue_btn button.u-form__sbt")).click({
    noWaitAfter: true,
    timeout: LONG_TIMEOUT,
  });
  await waitForConfirmPage(page);
  console.log("Дошел до финальной страницы проверки. Капчу/подтверждение не трогаю.");

  await waitEnter("Нажми Enter, чтобы закрыть браузер...");
  await context.close();
}

async function fillSingleNotification(page, payload) {
  await gotoTransportation(page);

  await fillApplicant(page, payload);
  await page.locator(".js_continue_btn button.u-form__sbt").click({ noWaitAfter: true, timeout: LONG_TIMEOUT });
  await waitForForm(page, "/transportation/customers/", "заказчика");
  console.log("Перешел на заказчика");

  await fillCustomer(page, payload);
  await clickNext(page, "/transportation/customers/");
  await waitForForm(page, "/transportation/carriers/", "перевозчика");
  console.log("Перешел на перевозчика");

  await fillCarrier(page, payload);
  await clickNext(page, "/transportation/carriers/");
  await waitForForm(page, "/transportation/vehicles/", "автобусов");
  console.log("Перешел на автобусы");

  await fillVehicles(page, payload);
  await clickNext(page, "/transportation/vehicles/");
  await waitForForm(page, "/transportation/drivers/", "водителей");
  console.log("Перешел на водителей");

  await fillDrivers(page, payload);
  await clickNext(page, "/transportation/drivers/");
  await waitForForm(page, "/transportation/route/", "маршрута");
  console.log("Перешел на маршрут");

  await fillRoute(page, payload);
  await page.locator(formDescendantSelector("/transportation/route/", ".js_continue_btn button.u-form__sbt")).click({
    noWaitAfter: true,
    timeout: LONG_TIMEOUT,
  });
  await waitForConfirmPage(page);
  console.log("Дошел до финальной страницы проверки. Капчу/подтверждение не трогаю.");
}

module.exports = {
  applyBusinessRules,
  attachDownloadSaver,
  fillSingleNotification,
};

if (require.main === module) {
  main().catch((error) => {
    console.error(error);
    process.exit(1);
  });
}
