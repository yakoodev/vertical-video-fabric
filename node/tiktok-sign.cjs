// Подпись X-Bogus и _signature для поста в TikTok (зовёт app/providers/tiktok.py).
//
// Тот же Signer и тот же вывод, что у browser.js из TiktokAutoUploader. Разница одна: Chromium идёт через прокси
// аккаунта. Signer открывает в браузере адрес www.tiktok.com, и без этого запрос уходил бы с настоящего IP сервера.
// Адрес прокси с паролем передаётся окружением (TIKTOK_SIGN_PROXY), не аргументом: аргументы видны в ps.
//
// node tiktok-sign.cjs <папка tiktok-signature> <url> <user agent>
const path = require("path");

function proxyOption(raw) {
  if (!raw) return null;
  const url = new URL(raw);
  const option = { server: `${url.protocol}//${url.host}` };
  if (url.username) option.username = decodeURIComponent(url.username);
  if (url.password) option.password = decodeURIComponent(url.password);
  return option;
}

(async function main() {
  const [signatureDir, url, userAgent] = process.argv.slice(2);
  const proxy = proxyOption(process.env.TIKTOK_SIGN_PROXY);
  const Signer = require(path.join(signatureDir, "index.js"));
  const signer = new Signer(url, userAgent);
  if (proxy) signer.options.proxy = proxy;
  try {
    await signer.init();
    const sign = await signer.sign(url);
    const navigator = await signer.navigator();
    console.log(JSON.stringify({ status: "ok", data: { ...sign, navigator } }));
  } finally {
    await signer.close();
  }
})().catch((err) => {
  // Только первая строка: в журнале Playwright дальше идёт вся команда запуска браузера и адрес с msToken.
  console.error(String((err && err.message) || err).split("\n")[0]);
  process.exit(1);
});
