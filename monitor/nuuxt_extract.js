// 乐淘详情页 NUXT 数据精确提取器
// 用法: node nuuxt_extract.js <PLATFORM> <ITEM_ID>
// 输出: JSON {title, price, cny, image, cond, content, sync_pending}
const https = require('https');
const vm = require('vm');

const COOKIE = 'Authori-zation=' + process.env.LETAO_TOKEN + '; lang=chs';
const UA = 'Mozilla/5.0 Chrome/153.0.0.0';
const platform = process.argv[2];
const iid = process.argv[3];

function get(url) {
  return new Promise((resolve, reject) => {
    https.get(url, { headers: { Cookie: COOKIE, 'User-Agent': UA, 'Accept-Language': 'zh-CN,zh;q=0.9' } }, res => {
      let d = '';
      res.on('data', c => d += c);
      res.on('end', () => resolve(d));
    }).on('error', reject).setTimeout(20000, () => reject(new Error('timeout')));
  });
}

(async () => {
  const out = { title: '', price: '', cny: '', image: '', cond: '', content: '', sync_pending: false };
  try {
    const html = await get(`https://letaoyifan.com/goods_detail/${platform}/${iid}`);
    let m = html.match(/window\.__NUXT__=([\s\S]+?)<\/script>/);
    if (!m) { out.sync_pending = true; console.log(JSON.stringify(out)); return; }
    let payload = m[1].trim().replace(/;$/, '');
    const sandbox = { window: {} };
    vm.createContext(sandbox);
    vm.runInContext('window.__NUXT__=' + payload, sandbox);
    const data = sandbox.window.__NUXT__.data && sandbox.window.__NUXT__.data[0];
    const main = data && (data.storeInfo || (function find(o) {
      for (const k of Object.keys(o || {})) {
        const v = o[k];
        if (v && typeof v === 'object' && !Array.isArray(v) && JSON.stringify(v).includes(iid)) return v;
      }
      return null;
    })(data));
    if (!main) { out.sync_pending = true; console.log(JSON.stringify(out)); return; }
    out.title = String(main.storeName || '').replace(/^undefined/, '').trim();
    out.price = String(main.price || '');
    out.cny = String(main.convertPrice || '');
    if (data.sliderImage && data.sliderImage[0]) out.image = data.sliderImage[0];
    else if (main.image) out.image = main.image;
    if (main.content) out.content = String(main.content).replace(/\s+/g, ' ').slice(0, 150);
    // 成色：从商品描述或标签里匹配
    const all = JSON.stringify(main);
    const conds = ['新,未使用', '接近未使用', '无明显划痕或污垢', '有一些划痕或污垢', '有划痕和污垢', '状态差', '脏污'];
    out.cond = conds.find(c => all.includes(c)) || '';
    if (!out.title || out.title === 'undefined' || !out.price) out.sync_pending = true;
    console.log(JSON.stringify(out));
  } catch (e) {
    out.sync_pending = true;
    out.err = String(e).slice(0, 80);
    console.log(JSON.stringify(out));
  }
})();
