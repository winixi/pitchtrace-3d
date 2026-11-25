import test from "node:test";
import assert from "node:assert/strict";

test("默认显示英文品牌，切换中文后显示场迹并记住选择", async (t) => {
  const storage = new Map();
  const brand = { dataset: { i18n: "brand.name" } };
  const browser = {
    navigator: { language: "zh-CN", languages: ["zh-CN", "en-US"] },
    localStorage: {
      getItem: (key) => storage.get(key),
      setItem: (key, locale) => storage.set(key, locale),
    },
    document: {
      documentElement: {},
      querySelectorAll: (selector) => (selector === "[data-i18n]" ? [brand] : []),
    },
  };
  for (const [name, value] of Object.entries(browser)) {
    const previous = Object.getOwnPropertyDescriptor(globalThis, name);
    Object.defineProperty(globalThis, name, {
      configurable: true,
      value,
    });
    t.after(() => {
      if (previous) Object.defineProperty(globalThis, name, previous);
      else delete globalThis[name];
    });
  }

  const { getLocale, setLocale, onChange } = await import("../src/i18n.js");
  assert.equal(getLocale(), "en");
  assert.equal(document.documentElement.lang, "en");
  assert.equal(brand.textContent, "PitchTrace 3D");

  const changes = [];
  const unsubscribe = onChange((locale) => changes.push(locale));
  t.after(unsubscribe);
  setLocale("en");
  assert.equal(storage.get("pitchtrace.locale"), "en");
  assert.deepEqual(changes, []);

  setLocale("zh-CN");
  assert.deepEqual(changes, ["zh-CN"]);
  assert.equal(storage.get("pitchtrace.locale"), "zh-CN");
  assert.equal(document.documentElement.lang, "zh-CN");
  assert.equal(brand.textContent, "场迹");

  const reloaded = await import("../src/i18n.js?reload");
  assert.equal(reloaded.getLocale(), "zh-CN");
  assert.equal(brand.textContent, "场迹");
  reloaded.setLocale("en");
  assert.equal(brand.textContent, "PitchTrace 3D");
});
