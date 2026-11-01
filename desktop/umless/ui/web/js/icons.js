// 内联线性图标：24×24 视图，stroke 1.75，圆角端点，风格统一。只含本地常量，可安全 raw()。
import { raw } from "./dom.js";

const P = {
  overview: '<rect x="3.5" y="3.5" width="7" height="8" rx="1.8"/><rect x="13.5" y="3.5" width="7" height="5" rx="1.8"/><rect x="13.5" y="11.5" width="7" height="9" rx="1.8"/><rect x="3.5" y="14.5" width="7" height="6" rx="1.8"/>',
  report: '<path d="M3.5 3.5v15a2 2 0 0 0 2 2h15"/><path d="m7.5 14.5 3.5-4 3 2.5 5-6"/>',
  practice: '<rect x="9" y="3" width="6" height="11.5" rx="3"/><path d="M5.5 11a6.5 6.5 0 0 0 13 0"/><path d="M12 17.5V21"/>',
  history: '<path d="M3.5 12a8.5 8.5 0 1 0 2.5-6"/><path d="M3.5 3.5V8H8"/><path d="M12 7.5V12l3 2"/>',
  settings: '<path d="M4 6.5h8.5"/><path d="M17.5 6.5H20"/><circle cx="15" cy="6.5" r="2.25"/><path d="M4 12h2.5"/><path d="M11.5 12H20"/><circle cx="9" cy="12" r="2.25"/><path d="M4 17.5h10.5"/><path d="M19.5 17.5H20"/><circle cx="17" cy="17.5" r="2.25"/>',
  search: '<circle cx="11" cy="11" r="6.5"/><path d="m20 20-4.2-4.2"/>',
  copy: '<rect x="8.5" y="8.5" width="12" height="12" rx="2.5"/><path d="M15.5 8.5V6a2.5 2.5 0 0 0-2.5-2.5H6A2.5 2.5 0 0 0 3.5 6v7A2.5 2.5 0 0 0 6 15.5h2.5"/>',
  trash: '<path d="M4 7h16"/><path d="M10 11v5.5"/><path d="M14 11v5.5"/><path d="m6 7 .9 11.6a2 2 0 0 0 2 1.9h6.2a2 2 0 0 0 2-1.9L18 7"/><path d="M9 7V5a1.5 1.5 0 0 1 1.5-1.5h3A1.5 1.5 0 0 1 15 5v2"/>',
  chevronRight: '<path d="m9.5 6 6 6-6 6"/>',
  chevronLeft: '<path d="m14.5 6-6 6 6 6"/>',
  chevronDown: '<path d="m6 9.5 6 6 6-6"/>',
  arrowRight: '<path d="M4.5 12h15"/><path d="m13.5 6 6 6-6 6"/>',
  arrowUp: '<path d="M12 19V5.5"/><path d="m6.5 11 5.5-5.5 5.5 5.5"/>',
  arrowDown: '<path d="M12 5v13.5"/><path d="m6.5 13 5.5 5.5 5.5-5.5"/>',
  check: '<path d="m5 12.5 4.5 4.5L19 7.5"/>',
  checkCircle: '<circle cx="12" cy="12" r="8.75"/><path d="m8.5 12.2 2.4 2.4 4.6-4.8"/>',
  x: '<path d="M6.5 6.5l11 11"/><path d="M17.5 6.5l-11 11"/>',
  plus: '<path d="M12 5v14"/><path d="M5 12h14"/>',
  minus: '<path d="M5 12h14"/>',
  refresh: '<path d="M19.5 10.5A7.5 7.5 0 0 0 6 6.8L4 8.8"/><path d="M4 4.5v4.3h4.3"/><path d="M4.5 13.5A7.5 7.5 0 0 0 18 17.2l2-2"/><path d="M20 19.5v-4.3h-4.3"/>',
  sparkles: '<path d="M11 3.5 12.7 8a2 2 0 0 0 1.3 1.3l4.5 1.7-4.5 1.7a2 2 0 0 0-1.3 1.3L11 18.5 9.3 14a2 2 0 0 0-1.3-1.3L3.5 11 8 9.3A2 2 0 0 0 9.3 8z"/><path d="M18.5 3v3.5"/><path d="M16.75 4.75h3.5"/><path d="M19 17v3"/><path d="M17.5 18.5h3"/>',
  bulb: '<path d="M9.5 17.5h5"/><path d="M10.5 20.5h3"/><path d="M12 3.5a5.75 5.75 0 0 0-3.4 10.4c.6.5.9 1.1.9 1.9v.2h5v-.2c0-.8.3-1.4.9-1.9A5.75 5.75 0 0 0 12 3.5z"/>',
  alert: '<path d="M10.3 4.3 2.9 17.2a2 2 0 0 0 1.7 3h14.8a2 2 0 0 0 1.7-3L13.7 4.3a2 2 0 0 0-3.4 0z"/><path d="M12 9.5v4"/><path d="M12 16.8v.2"/>',
  info: '<circle cx="12" cy="12" r="8.75"/><path d="M12 11v5"/><path d="M12 7.8v.2"/>',
  download: '<path d="M12 4v11"/><path d="m7 10.5 5 5 5-5"/><path d="M5 20h14"/>',
  export: '<path d="M12 15V4"/><path d="m7 8.5 5-5 5 5"/><path d="M5 13.5V18a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2v-4.5"/>',
  eye: '<path d="M2.5 12S6 5.5 12 5.5 21.5 12 21.5 12 18 18.5 12 18.5 2.5 12 2.5 12z"/><circle cx="12" cy="12" r="2.75"/>',
  eyeOff: '<path d="M3.5 3.5l17 17"/><path d="M10.5 5.6c.5-.1 1-.1 1.5-.1 6 0 9.5 6.5 9.5 6.5a16 16 0 0 1-2.5 3.3"/><path d="M6.6 6.7C4 8.5 2.5 12 2.5 12S6 18.5 12 18.5c1.6 0 3-.4 4.3-1.1"/><path d="M10 10a2.75 2.75 0 0 0 4 4"/>',
  folder: '<path d="M3.5 7.5a2 2 0 0 1 2-2h3.7l2 2.2h7.3a2 2 0 0 1 2 2V17a2 2 0 0 1-2 2h-13a2 2 0 0 1-2-2z"/>',
  file: '<path d="M14 3.5H7.5a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2h9a2 2 0 0 0 2-2V8z"/><path d="M14 3.5V8h4.5"/>',
  doc: '<path d="M14 3.5H7.5a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2h9a2 2 0 0 0 2-2V8z"/><path d="M14 3.5V8h4.5"/><path d="M9 12.5h6"/><path d="M9 16h4"/>',
  keyboard: '<rect x="2.5" y="6" width="19" height="12" rx="2.5"/><path d="M6.5 10h.01M10 10h.01M14 10h.01M17.5 10h.01M8 14h8"/>',
  power: '<path d="M12 3.5v7.5"/><path d="M6.7 6.8a7.5 7.5 0 1 0 10.6 0"/>',
  shield: '<path d="M12 3.5 5 6.2v5.3c0 4.3 3 8 7 9 4-1 7-4.7 7-9V6.2z"/><path d="m9 12 2.2 2.2L15.5 10"/>',
  book: '<path d="M5 5a1.5 1.5 0 0 1 1.5-1.5H19v14H6.5A1.5 1.5 0 0 0 5 19z"/><path d="M5 19a1.5 1.5 0 0 0 1.5 1.5H19"/><path d="M9 7.5h6"/>',
  chat: '<path d="M20.5 11.5a8 8 0 0 1-11.7 7.1L4 20l1.4-4.5A8 8 0 1 1 20.5 11.5z"/>',
  mail: '<rect x="3" y="5" width="18" height="14" rx="2.5"/><path d="m3.5 7.5 8.5 6 8.5-6"/>',
  code: '<path d="m8 8-4 4 4 4"/><path d="m16 8 4 4-4 4"/><path d="m13.5 5-3 14"/>',
  wand: '<path d="m4 20 10.5-10.5"/><path d="m12.5 7.5 3 3"/><path d="M17.5 3v3"/><path d="M16 4.5h3"/><path d="M20 9.5v2.5"/><path d="M18.75 10.75h2.5"/><path d="M9 3.5v2"/><path d="M8 4.5h2"/>',
  wave: '<path d="M4 10v4"/><path d="M8 6.5v11"/><path d="M12 9v6"/><path d="M16 4v16"/><path d="M20 10v4"/>',
  layers: '<path d="m12 3.5 8.5 4.5-8.5 4.5L3.5 8z"/><path d="m3.5 12.5 8.5 4.5 8.5-4.5"/><path d="m3.5 16.5 8.5 4.5 8.5-4.5"/>',
  target: '<circle cx="12" cy="12" r="8.5"/><circle cx="12" cy="12" r="4.5"/><circle cx="12" cy="12" r=".9" fill="currentColor"/>',
  trophy: '<path d="M8 4h8v5.5a4 4 0 0 1-8 0z"/><path d="M16 5.5h2.5a1 1 0 0 1 1 1v.5a3.5 3.5 0 0 1-3.6 3.5"/><path d="M8 5.5H5.5a1 1 0 0 0-1 1v.5a3.5 3.5 0 0 0 3.6 3.5"/><path d="M12 13.5V17"/><path d="M8.5 20.5h7"/><path d="M9.5 20.5c0-2 1-3.5 2.5-3.5s2.5 1.5 2.5 3.5"/>',
  archive: '<rect x="3" y="4" width="18" height="4.5" rx="1.5"/><path d="M5 8.5V18a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8.5"/><path d="M10 12.5h4"/>',
  undo: '<path d="M9 14 4 9l5-5"/><path d="M4 9h10.5a5.5 5.5 0 0 1 0 11H11"/>',
  repeat: '<path d="m17 3 3 3-3 3"/><path d="M4 11.5V10a4 4 0 0 1 4-4h12"/><path d="m7 21-3-3 3-3"/><path d="M20 12.5V14a4 4 0 0 1-4 4H4"/>',
  pen: '<path d="M12.5 20h8"/><path d="M16.4 3.6a2.1 2.1 0 0 1 3 3L7.5 18.5l-4 1 1-4z"/>',
  structure: '<path d="M4 5.5h16"/><path d="M9 12h11"/><path d="M9 18.5h11"/><path d="M4 5.5v10a3 3 0 0 0 3 3"/><path d="M4 9a3 3 0 0 0 3 3"/>',
  sun: '<circle cx="12" cy="12" r="3.75"/><path d="M12 3v1.8M12 19.2V21M5.6 5.6l1.3 1.3M17.1 17.1l1.3 1.3M3 12h1.8M19.2 12H21M5.6 18.4l1.3-1.3M17.1 6.9l1.3-1.3"/>',
  moon: '<path d="M20 14.5A8.25 8.25 0 1 1 9.5 4a6.5 6.5 0 0 0 10.5 10.5z"/>',
  monitor: '<rect x="3" y="4" width="18" height="12.5" rx="2"/><path d="M8.5 20h7"/><path d="M12 16.5V20"/>',
  stop: '<rect x="7" y="7" width="10" height="10" rx="2.2" fill="currentColor" stroke="none"/>',
  play: '<path d="M8 5.8v12.4a1 1 0 0 0 1.5.9l9.9-6.2a1 1 0 0 0 0-1.8L9.5 4.9A1 1 0 0 0 8 5.8z"/>',
  user: '<circle cx="12" cy="8.5" r="3.75"/><path d="M5 20a7 7 0 0 1 14 0"/>',
  clock: '<circle cx="12" cy="12" r="8.75"/><path d="M12 7.5V12l3 2"/>',
  zap: '<path d="M13 2.5 4.5 13.5H12l-1 8 8.5-11H12z"/>',
  cloud: '<path d="M7 18.5a4.5 4.5 0 0 1-.6-9 6 6 0 0 1 11.5 1.6A3.8 3.8 0 0 1 17.5 18.5z"/>',
  cpu: '<rect x="6" y="6" width="12" height="12" rx="2.2"/><path d="M9.5 9.5h5v5h-5z"/><path d="M9.5 3v3M14.5 3v3M9.5 18v3M14.5 18v3M3 9.5h3M3 14.5h3M18 9.5h3M18 14.5h3"/>',
  external: '<path d="M14 4h6v6"/><path d="M20 4l-8.5 8.5"/><path d="M18 14v4a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h4"/>',
  volume: '<path d="M11 5.5 6.5 9H3.5v6h3l4.5 3.5z"/><path d="M15.5 9a4.5 4.5 0 0 1 0 6"/><path d="M18.3 6.2a8.5 8.5 0 0 1 0 11.6"/>',
  quote: '<path d="M10 7.5H6.5a1 1 0 0 0-1 1V12a1 1 0 0 0 1 1H9v.5c0 1.8-1 3.1-2.5 3.5"/><path d="M18.5 7.5H15a1 1 0 0 0-1 1V12a1 1 0 0 0 1 1h2.5v.5c0 1.8-1 3.1-2.5 3.5"/>',
  calendar: '<rect x="3.5" y="5" width="17" height="15.5" rx="2.5"/><path d="M3.5 10h17"/><path d="M8 3v4"/><path d="M16 3v4"/>',
  list: '<path d="M9 6.5h11M9 12h11M9 17.5h11"/><path d="M4.5 6.5h.01M4.5 12h.01M4.5 17.5h.01"/>',
  trend: '<path d="m3.5 16.5 5.5-5.5 4 4 7.5-7.5"/><path d="M15 7.5h5.5V13"/>',
  more: '<circle cx="5.5" cy="12" r="1.1" fill="currentColor"/><circle cx="12" cy="12" r="1.1" fill="currentColor"/><circle cx="18.5" cy="12" r="1.1" fill="currentColor"/>',
  type: '<path d="M5 7.5V5h14v2.5"/><path d="M12 5v14"/><path d="M9 19h6"/>',
  ban: '<circle cx="12" cy="12" r="8.75"/><path d="m5.8 5.8 12.4 12.4"/>',
  database: '<ellipse cx="12" cy="5.75" rx="7.5" ry="2.75"/><path d="M4.5 5.75v12.5c0 1.5 3.4 2.75 7.5 2.75s7.5-1.25 7.5-2.75V5.75"/><path d="M4.5 12c0 1.5 3.4 2.75 7.5 2.75s7.5-1.25 7.5-2.75"/>',
  flag: '<path d="M5 21V4.5"/><path d="M5 4.5h11l-2 4 2 4H5"/>',
  hand: '<path d="M8.5 11V5.5a1.5 1.5 0 0 1 3 0V10"/><path d="M11.5 9.5V4a1.5 1.5 0 0 1 3 0v6"/><path d="M14.5 9.5V5.5a1.5 1.5 0 0 1 3 0V14a7 7 0 0 1-7 7h-.4a6 6 0 0 1-4.6-2.2l-2.6-3.3a1.5 1.5 0 0 1 2.3-1.9L8.5 16V11"/>',
  timer: '<circle cx="12" cy="13.5" r="7.5"/><path d="M12 9.5v4l2.5 1.5"/><path d="M9.5 2.5h5"/>',
  gauge: '<path d="M4.2 17.5a8.5 8.5 0 1 1 15.6 0"/><path d="m12 13.5 4-4"/><circle cx="12" cy="13.5" r="1.2"/>',
};

/** 返回内联 SVG（SafeHtml） */
export function icon(name, size = 18, cls = "") {
  const d = P[name];
  if (!d) return raw("");
  return raw(
    `<svg class="i ${cls}" width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${d}</svg>`,
  );
}

let logoSeq = 0;
/** 品牌标识：青绿渐变圆角方块 + 对称声波 */
export function logo(size = 28, cls = "brand-mark") {
  const id = `ulg${++logoSeq}`;
  return raw(`<svg class="${cls}" width="${size}" height="${size}" viewBox="0 0 32 32" aria-hidden="true">
    <defs><linearGradient id="${id}" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0" stop-color="#2dd4bf"/><stop offset="1" stop-color="#0d9488"/></linearGradient></defs>
    <rect width="32" height="32" rx="9" fill="url(#${id})"/>
    <g stroke="#fff" stroke-width="2.6" stroke-linecap="round">
      <path d="M8 14v4" opacity=".75"/><path d="M12 11.5v9"/><path d="M16 8.5v15"/><path d="M20 11.5v9"/><path d="M24 14v4" opacity=".75"/>
    </g></svg>`);
}
