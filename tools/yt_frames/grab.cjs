// Grab clean 1280x720 frames from a YouTube video at given seconds, using real Chrome.
// Why: on this PC the video CDN only works in a browser (yt-dlp/curl hang), so frames are taken from the player.
//
// Usage: node tools/yt_frames/grab.cjs <videoId> <outDir> <sec,sec,...|every:N[:from-to]>
// Env:   PLAYWRIGHT_CORE = path to a playwright-core package (e.g. one from the npx cache of the Playwright MCP).
// Output: <outDir>/MMmSSs.png for each second; prints one line per frame and "DONE n" at the end.
const path = require('path');
const pwPath = process.env.PLAYWRIGHT_CORE;
if (!pwPath) { console.error('set PLAYWRIGHT_CORE'); process.exit(2); }
const { chromium } = require(pwPath);
const fs = require('fs');

const [id, outDir, spec] = process.argv.slice(2);
if (!id || !outDir || !spec) { console.error('usage: grab.cjs <id> <outDir> <secs|every:N[:a-b]>'); process.exit(2); }
fs.mkdirSync(outDir, { recursive: true });

function parseSpec(s, duration) {
  if (s.startsWith('every:')) {
    const [, step, range] = s.split(':');
    let [a, b] = range ? range.split('-').map(Number) : [0, duration];
    const out = []; for (let t = a; t <= Math.min(b, duration - 1); t += Number(step)) out.push(Math.round(t));
    return out;
  }
  return s.split(',').map(Number).filter(n => !isNaN(n));
}
const name = t => `${String(Math.floor(t / 60)).padStart(2, '0')}m${String(t % 60).padStart(2, '0')}s.png`;

(async () => {
  const browser = await chromium.launch({ channel: 'chrome', headless: false, args: ['--autoplay-policy=no-user-gesture-required', '--window-position=-2400,0', '--window-size=1300,800'] });
  const page = await browser.newPage({ viewport: { width: 1280, height: 720 } });
  const watchdog = setTimeout(() => { console.error('watchdog timeout'); process.exit(3); }, 30 * 60 * 1000);
  let ok = false;
  for (let attempt = 0; attempt < 3 && !ok; attempt++) {
    await page.goto(`https://www.youtube.com/watch?v=${id}`, { timeout: 60000 });
    await page.waitForTimeout(4000);
    await page.evaluate(async () => { const v = document.querySelector('video.html5-main-video'); if (!v) return; v.muted = true;
      await Promise.race([v.play().catch(() => {}), new Promise(r => setTimeout(r, 3000))]); });
    for (let i = 0; i < 30; i++) {
      await page.waitForTimeout(1000);
      const st = await page.evaluate(() => { const v = document.querySelector('video.html5-main-video');
        return v ? { r: v.readyState, ad: !!document.querySelector('.ad-showing') } : { r: 0 }; });
      if (st.r >= 3 && !st.ad) { ok = true; break; }
    }
  }
  if (!ok) { console.error('video did not load'); await browser.close(); process.exit(4); }
  const duration = await page.evaluate(() => document.querySelector('video.html5-main-video').duration);
  // show only the video element: no sidebar, controls or captions
  await page.addStyleTag({ content: 'html,body,ytd-app{visibility:hidden!important;background:#000!important} video.html5-main-video{visibility:visible!important;position:fixed!important;top:0!important;left:0!important;width:1280px!important;height:720px!important;z-index:2147483647!important;object-fit:contain!important}' });
  const secs = parseSpec(spec, duration);
  let n = 0;
  for (const t of secs) {
    await page.evaluate(async t => { const v = document.querySelector('video.html5-main-video'); v.pause();
      await new Promise(r => { const to = setTimeout(r, 10000); v.addEventListener('seeked', () => { clearTimeout(to); r(); }, { once: true }); v.currentTime = t; });
      for (let i = 0; i < 40 && v.readyState < 2; i++) await new Promise(r => setTimeout(r, 200)); }, t);
    await page.waitForTimeout(300);
    const r = await page.evaluate(() => document.querySelector('video.html5-main-video').readyState);
    await page.screenshot({ path: path.join(outDir, name(t)), timeout: 15000 });
    n++; console.log(`${name(t)} ready=${r}`);
  }
  clearTimeout(watchdog);
  await browser.close();
  console.log(`DONE ${n}`);
})().catch(e => { console.error(e); process.exit(1); });
