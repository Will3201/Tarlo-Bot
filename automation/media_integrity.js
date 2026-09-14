/* Pure JavaScript: usable in code mode without filesystem, crypto, or atob. */
function decodeBase64(text) {
  const s = text.replace(/\s/g, '');
  if (!/^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$/.test(s)) throw Error('Invalid base64');
  const alphabet = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/';
  const padding = s.endsWith('==') ? 2 : s.endsWith('=') ? 1 : 0;
  const out = new Uint8Array(s.length / 4 * 3 - padding);
  let offset = 0;
  for (let i = 0; i < s.length; i += 4) {
    const a = alphabet.indexOf(s[i]), b = alphabet.indexOf(s[i + 1]);
    const c = s[i + 2] === '=' ? 0 : alphabet.indexOf(s[i + 2]);
    const d = s[i + 3] === '=' ? 0 : alphabet.indexOf(s[i + 3]);
    const n = (a << 18) | (b << 12) | (c << 6) | d;
    if (offset < out.length) out[offset++] = n >>> 16;
    if (offset < out.length) out[offset++] = n >>> 8;
    if (offset < out.length) out[offset++] = n;
    if (i + 4 === s.length && ((padding === 2 && (b & 15)) || (padding === 1 && (c & 3)))) throw Error('Non-canonical base64');
  }
  return out;
}

function sha256(bytes) {
  const K = [0x428a2f98,0x71374491,0xb5c0fbcf,0xe9b5dba5,0x3956c25b,0x59f111f1,0x923f82a4,0xab1c5ed5,0xd807aa98,0x12835b01,0x243185be,0x550c7dc3,0x72be5d74,0x80deb1fe,0x9bdc06a7,0xc19bf174,0xe49b69c1,0xefbe4786,0x0fc19dc6,0x240ca1cc,0x2de92c6f,0x4a7484aa,0x5cb0a9dc,0x76f988da,0x983e5152,0xa831c66d,0xb00327c8,0xbf597fc7,0xc6e00bf3,0xd5a79147,0x06ca6351,0x14292967,0x27b70a85,0x2e1b2138,0x4d2c6dfc,0x53380d13,0x650a7354,0x766a0abb,0x81c2c92e,0x92722c85,0xa2bfe8a1,0xa81a664b,0xc24b8b70,0xc76c51a3,0xd192e819,0xd6990624,0xf40e3585,0x106aa070,0x19a4c116,0x1e376c08,0x2748774c,0x34b0bcb5,0x391c0cb3,0x4ed8aa4a,0x5b9cca4f,0x682e6ff3,0x748f82ee,0x78a5636f,0x84c87814,0x8cc70208,0x90befffa,0xa4506ceb,0xbef9a3f7,0xc67178f2];
  const h = [0x6a09e667,0xbb67ae85,0x3c6ef372,0xa54ff53a,0x510e527f,0x9b05688c,0x1f83d9ab,0x5be0cd19];
  const padded = new Uint8Array(Math.ceil((bytes.length + 9) / 64) * 64);
  padded.set(bytes); padded[bytes.length] = 128;
  const view = new DataView(padded.buffer);
  view.setUint32(padded.length - 8, Math.floor(bytes.length / 536870912));
  view.setUint32(padded.length - 4, (bytes.length * 8) >>> 0);
  const rotate = (x, n) => (x >>> n) | (x << (32 - n));
  const w = new Uint32Array(64);
  for (let offset = 0; offset < padded.length; offset += 64) {
    for (let i = 0; i < 16; i++) w[i] = view.getUint32(offset + i * 4);
    for (let i = 16; i < 64; i++) {
      const x = w[i - 15], y = w[i - 2];
      w[i] = w[i - 16] + (rotate(x, 7) ^ rotate(x, 18) ^ (x >>> 3)) + w[i - 7] + (rotate(y, 17) ^ rotate(y, 19) ^ (y >>> 10));
    }
    let [a,b,c,d,e,f,g,j] = h;
    for (let i = 0; i < 64; i++) {
      const t1 = (j + (rotate(e,6)^rotate(e,11)^rotate(e,25)) + ((e&f)^(~e&g)) + K[i] + w[i]) >>> 0;
      const t2 = ((rotate(a,2)^rotate(a,13)^rotate(a,22)) + ((a&b)^(a&c)^(b&c))) >>> 0;
      j=g;g=f;f=e;e=(d+t1)>>>0;d=c;c=b;b=a;a=(t1+t2)>>>0;
    }
    [a,b,c,d,e,f,g,j].forEach((x,i)=>{h[i]=(h[i]+x)>>>0;});
  }
  return h.map(x=>x.toString(16).padStart(8,'0')).join('');
}

function imageHeader(bytes) {
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  if (bytes.length >= 24 && [137,80,78,71,13,10,26,10].every((v,i)=>bytes[i]===v)) {
    return {format:'PNG',width:view.getUint32(16),height:view.getUint32(20)};
  }
  if (bytes.length < 4 || bytes[0] !== 255 || bytes[1] !== 216) throw Error('Unsupported image');
  let p=2;
  const sof = new Set([192,193,194,195,197,198,199,201,202,203,205,206,207]);
  while (p + 4 <= bytes.length) {
    if (bytes[p++] !== 255) throw Error('Invalid JPEG marker');
    while (bytes[p] === 255) p++;
    const marker=bytes[p++];
    if (marker === 217 || marker === 218) break;
    if (marker === 1 || (marker >= 208 && marker <= 215)) continue;
    if (p + 2 > bytes.length) break;
    const length=view.getUint16(p);
    if (length < 2 || p + length > bytes.length) throw Error('Truncated JPEG');
    if (sof.has(marker)) {
      if (length < 8) throw Error('Invalid JPEG frame');
      return {format:'JPEG',width:view.getUint16(p+5),height:view.getUint16(p+3),components:bytes[p+7]};
    }
    p+=length;
  }
  throw Error('JPEG dimensions unavailable');
}

function verifyImage(base64, expectedSha, options={}) {
  const bytes=decodeBase64(base64), digest=sha256(bytes), header=imageHeader(bytes);
  if (digest !== expectedSha) throw Error('SHA256 mismatch');
  if (options.maxSide && Math.max(header.width,header.height)>options.maxSide) throw Error('Image too large');
  if (options.format && header.format!==options.format) throw Error('Wrong format');
  if (options.portrait34 && header.width*4!==header.height*3) throw Error('Expected 3:4');
  if (header.format==='JPEG' && header.components!==3) throw Error('Expected three-component JPEG');
  return {sha256:digest,...header};
}
