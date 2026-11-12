/**
 * Значки плоской темы — набор Lucide (https://lucide.dev, lucide-static 0.460.0), лицензия ISC
 * (части из Feather — MIT); текст лицензии — `LUCIDE_LICENSE.txt` рядом и docs/motion/flat-ui.md.
 *
 * Значки — пути SVG в сетке 24×24 с обводкой 2 px, круглые концы и стыки (как в самом наборе). Каждый элемент
 * значка — отдельная строка пути (первое `m` элемента в SVG абсолютное, поэтому склеивать их нельзя).
 * Подмножество сгенерировано из `icon-nodes.json` пакета (circle/rect/line/polyline → path) — чужих
 * логотипов в нём нет. Новый значок: имя из lucide.dev → строки путей сюда же.
 */

export const LUCIDE_VERSION = "0.460.0";
export const LUCIDE_LICENSE = "ISC";

export const FLAT_ICONS: Record<string, string[]> = {
  "check": ["M20 6 9 17l-5-5"],
  "circle-check": ["M2 12a10 10 0 1 0 20 0a10 10 0 1 0 -20 0Z", "m9 12 2 2 4-4"],
  "circle-check-big": ["M21.801 10A10 10 0 1 1 17 3.335", "m9 11 3 3L22 4"],
  "x": ["M18 6 6 18", "m6 6 12 12"],
  "circle-x": ["M2 12a10 10 0 1 0 20 0a10 10 0 1 0 -20 0Z", "m15 9-6 6", "m9 9 6 6"],
  "triangle-alert": ["m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3", "M12 9v4", "M12 17h.01"],
  "circle-alert": ["M2 12a10 10 0 1 0 20 0a10 10 0 1 0 -20 0Z", "M12 8L12 12", "M12 16L12.01 16"],
  "info": ["M2 12a10 10 0 1 0 20 0a10 10 0 1 0 -20 0Z", "M12 16v-4", "M12 8h.01"],
  "bell": ["M6 8a6 6 0 0 1 12 0c0 7 3 9 3 9H3s3-2 3-9", "M10.3 21a1.94 1.94 0 0 0 3.4 0"],
  "bell-ring": ["M6 8a6 6 0 0 1 12 0c0 7 3 9 3 9H3s3-2 3-9", "M10.3 21a1.94 1.94 0 0 0 3.4 0", "M4 2C2.8 3.7 2 5.7 2 8", "M22 8c0-2.3-.8-4.3-2-6"],
  "message-circle": ["M7.9 20A9 9 0 1 0 4 16.1L2 22Z"],
  "message-square": ["M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"],
  "send": ["M14.536 21.686a.5.5 0 0 0 .937-.024l6.5-19a.496.496 0 0 0-.635-.635l-19 6.5a.5.5 0 0 0-.024.937l7.93 3.18a2 2 0 0 1 1.112 1.11z", "m21.854 2.147-10.94 10.939"],
  "heart": ["M19 14c1.49-1.46 3-3.21 3-5.5A5.5 5.5 0 0 0 16.5 3c-1.76 0-3 .5-4.5 2-1.5-1.5-2.74-2-4.5-2A5.5 5.5 0 0 0 2 8.5c0 2.3 1.5 4.05 3 5.5l7 7Z"],
  "thumbs-up": ["M7 10v12", "M15 5.88 14 10h5.83a2 2 0 0 1 1.92 2.56l-2.33 8A2 2 0 0 1 17.5 22H4a2 2 0 0 1-2-2v-8a2 2 0 0 1 2-2h2.76a2 2 0 0 0 1.79-1.11L12 2a3.13 3.13 0 0 1 3 3.88Z"],
  "thumbs-down": ["M17 14V2", "M9 18.12 10 14H4.17a2 2 0 0 1-1.92-2.56l2.33-8A2 2 0 0 1 6.5 2H20a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2h-2.76a2 2 0 0 0-1.79 1.11L12 22a3.13 3.13 0 0 1-3-3.88Z"],
  "eye": ["M2.062 12.348a1 1 0 0 1 0-.696 10.75 10.75 0 0 1 19.876 0 1 1 0 0 1 0 .696 10.75 10.75 0 0 1-19.876 0", "M9 12a3 3 0 1 0 6 0a3 3 0 1 0 -6 0Z"],
  "play": ["M6 3L20 12L6 21L6 3Z"],
  "pause": ["M15 4h2a1 1 0 0 1 1 1v14a1 1 0 0 1 -1 1h-2a1 1 0 0 1 -1 -1v-14a1 1 0 0 1 1 -1Z", "M7 4h2a1 1 0 0 1 1 1v14a1 1 0 0 1 -1 1h-2a1 1 0 0 1 -1 -1v-14a1 1 0 0 1 1 -1Z"],
  "hourglass": ["M5 22h14", "M5 2h14", "M17 22v-4.172a2 2 0 0 0-.586-1.414L12 12l-4.414 4.414A2 2 0 0 0 7 17.828V22", "M7 2v4.172a2 2 0 0 0 .586 1.414L12 12l4.414-4.414A2 2 0 0 0 17 6.172V2"],
  "clock": ["M2 12a10 10 0 1 0 20 0a10 10 0 1 0 -20 0Z", "M12 6L12 12L16 14"],
  "timer": ["M10 2L14 2", "M12 14L15 11", "M4 14a8 8 0 1 0 16 0a8 8 0 1 0 -16 0Z"],
  "calendar": ["M8 2v4", "M16 2v4", "M5 4h14a2 2 0 0 1 2 2v14a2 2 0 0 1 -2 2h-14a2 2 0 0 1 -2 -2v-14a2 2 0 0 1 2 -2Z", "M3 10h18"],
  "user": ["M19 21v-2a4 4 0 0 0-4-4H9a4 4 0 0 0-4 4v2", "M8 7a4 4 0 1 0 8 0a4 4 0 1 0 -8 0Z"],
  "users": ["M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2", "M5 7a4 4 0 1 0 8 0a4 4 0 1 0 -8 0Z", "M22 21v-2a4 4 0 0 0-3-3.87", "M16 3.13a4 4 0 0 1 0 7.75"],
  "star": ["M11.525 2.295a.53.53 0 0 1 .95 0l2.31 4.679a2.123 2.123 0 0 0 1.595 1.16l5.166.756a.53.53 0 0 1 .294.904l-3.736 3.638a2.123 2.123 0 0 0-.611 1.878l.882 5.14a.53.53 0 0 1-.771.56l-4.618-2.428a2.122 2.122 0 0 0-1.973 0L6.396 21.01a.53.53 0 0 1-.77-.56l.881-5.139a2.122 2.122 0 0 0-.611-1.879L2.16 9.795a.53.53 0 0 1 .294-.906l5.165-.755a2.122 2.122 0 0 0 1.597-1.16z"],
  "zap": ["M4 14a1 1 0 0 1-.78-1.63l9.9-10.2a.5.5 0 0 1 .86.46l-1.92 6.02A1 1 0 0 0 13 10h7a1 1 0 0 1 .78 1.63l-9.9 10.2a.5.5 0 0 1-.86-.46l1.92-6.02A1 1 0 0 0 11 14z"],
  "flame": ["M8.5 14.5A2.5 2.5 0 0 0 11 12c0-1.38-.5-2-1-3-1.072-2.143-.224-4.054 2-6 .5 2.5 2 4.9 4 6.5 2 1.6 3 3.5 3 5.5a7 7 0 1 1-14 0c0-1.153.433-2.294 1-3a2.5 2.5 0 0 0 2.5 2.5z"],
  "trending-up": ["M22 7L13.5 15.5L8.5 10.5L2 17", "M16 7L22 7L22 13"],
  "trending-down": ["M22 17L13.5 8.5L8.5 13.5L2 7", "M16 17L22 17L22 11"],
  "chart-column": ["M3 3v16a2 2 0 0 0 2 2h16", "M18 17V9", "M13 17V5", "M8 17v-3"],
  "chart-pie": ["M21 12c.552 0 1.005-.449.95-.998a10 10 0 0 0-8.953-8.951c-.55-.055-.998.398-.998.95v8a1 1 0 0 0 1 1z", "M21.21 15.89A10 10 0 1 1 8 2.83"],
  "dollar-sign": ["M12 2L12 22", "M17 5H9.5a3.5 3.5 0 0 0 0 7h5a3.5 3.5 0 0 1 0 7H6"],
  "wallet": ["M19 7V4a1 1 0 0 0-1-1H5a2 2 0 0 0 0 4h15a1 1 0 0 1 1 1v4h-3a2 2 0 0 0 0 4h3a1 1 0 0 0 1-1v-2a1 1 0 0 0-1-1", "M3 5v14a2 2 0 0 0 2 2h15a1 1 0 0 0 1-1v-4"],
  "credit-card": ["M4 5h16a2 2 0 0 1 2 2v10a2 2 0 0 1 -2 2h-16a2 2 0 0 1 -2 -2v-10a2 2 0 0 1 2 -2Z", "M2 10L22 10"],
  "shopping-cart": ["M7 21a1 1 0 1 0 2 0a1 1 0 1 0 -2 0Z", "M18 21a1 1 0 1 0 2 0a1 1 0 1 0 -2 0Z", "M2.05 2.05h2l2.66 12.42a2 2 0 0 0 2 1.58h9.78a2 2 0 0 0 1.95-1.57l1.65-7.43H5.12"],
  "gift": ["M4 8h16a1 1 0 0 1 1 1v2a1 1 0 0 1 -1 1h-16a1 1 0 0 1 -1 -1v-2a1 1 0 0 1 1 -1Z", "M12 8v13", "M19 12v7a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2v-7", "M7.5 8a2.5 2.5 0 0 1 0-5A4.8 8 0 0 1 12 8a4.8 8 0 0 1 4.5-5 2.5 2.5 0 0 1 0 5"],
  "lock": ["M5 11h14a2 2 0 0 1 2 2v7a2 2 0 0 1 -2 2h-14a2 2 0 0 1 -2 -2v-7a2 2 0 0 1 2 -2Z", "M7 11V7a5 5 0 0 1 10 0v4"],
  "shield-check": ["M20 13c0 5-3.5 7.5-7.66 8.95a1 1 0 0 1-.67-.01C7.5 20.5 4 18 4 13V6a1 1 0 0 1 1-1c2 0 4.5-1.2 6.24-2.72a1.17 1.17 0 0 1 1.52 0C14.51 3.81 17 5 19 5a1 1 0 0 1 1 1z", "m9 12 2 2 4-4"],
  "globe": ["M2 12a10 10 0 1 0 20 0a10 10 0 1 0 -20 0Z", "M12 2a14.5 14.5 0 0 0 0 20 14.5 14.5 0 0 0 0-20", "M2 12h20"],
  "link": ["M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71", "M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"],
  "search": ["M3 11a8 8 0 1 0 16 0a8 8 0 1 0 -16 0Z", "m21 21-4.3-4.3"],
  "settings": ["M12.22 2h-.44a2 2 0 0 0-2 2v.18a2 2 0 0 1-1 1.73l-.43.25a2 2 0 0 1-2 0l-.15-.08a2 2 0 0 0-2.73.73l-.22.38a2 2 0 0 0 .73 2.73l.15.1a2 2 0 0 1 1 1.72v.51a2 2 0 0 1-1 1.74l-.15.09a2 2 0 0 0-.73 2.73l.22.38a2 2 0 0 0 2.73.73l.15-.08a2 2 0 0 1 2 0l.43.25a2 2 0 0 1 1 1.73V20a2 2 0 0 0 2 2h.44a2 2 0 0 0 2-2v-.18a2 2 0 0 1 1-1.73l.43-.25a2 2 0 0 1 2 0l.15.08a2 2 0 0 0 2.73-.73l.22-.39a2 2 0 0 0-.73-2.73l-.15-.08a2 2 0 0 1-1-1.74v-.5a2 2 0 0 1 1-1.74l.15-.09a2 2 0 0 0 .73-2.73l-.22-.38a2 2 0 0 0-2.73-.73l-.15.08a2 2 0 0 1-2 0l-.43-.25a2 2 0 0 1-1-1.73V4a2 2 0 0 0-2-2z", "M9 12a3 3 0 1 0 6 0a3 3 0 1 0 -6 0Z"],
  "house": ["M15 21v-8a1 1 0 0 0-1-1h-4a1 1 0 0 0-1 1v8", "M3 10a2 2 0 0 1 .709-1.528l7-5.999a2 2 0 0 1 2.582 0l7 5.999A2 2 0 0 1 21 10v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"],
  "sparkles": ["M9.937 15.5A2 2 0 0 0 8.5 14.063l-6.135-1.582a.5.5 0 0 1 0-.962L8.5 9.936A2 2 0 0 0 9.937 8.5l1.582-6.135a.5.5 0 0 1 .963 0L14.063 8.5A2 2 0 0 0 15.5 9.937l6.135 1.581a.5.5 0 0 1 0 .964L15.5 14.063a2 2 0 0 0-1.437 1.437l-1.582 6.135a.5.5 0 0 1-.963 0z", "M20 3v4", "M22 5h-4", "M4 17v2", "M5 18H3"],
  "rocket": ["M4.5 16.5c-1.5 1.26-2 5-2 5s3.74-.5 5-2c.71-.84.7-2.13-.09-2.91a2.18 2.18 0 0 0-2.91-.09z", "m12 15-3-3a22 22 0 0 1 2-3.95A12.88 12.88 0 0 1 22 2c0 2.72-.78 7.5-6 11a22.35 22.35 0 0 1-4 2z", "M9 12H4s.55-3.03 2-4c1.62-1.08 5 0 5 0", "M12 15v5s3.03-.55 4-2c1.08-1.62 0-5 0-5"],
  "target": ["M2 12a10 10 0 1 0 20 0a10 10 0 1 0 -20 0Z", "M6 12a6 6 0 1 0 12 0a6 6 0 1 0 -12 0Z", "M10 12a2 2 0 1 0 4 0a2 2 0 1 0 -4 0Z"],
  "trophy": ["M6 9H4.5a2.5 2.5 0 0 1 0-5H6", "M18 9h1.5a2.5 2.5 0 0 0 0-5H18", "M4 22h16", "M10 14.66V17c0 .55-.47.98-.97 1.21C7.85 18.75 7 20.24 7 22", "M14 14.66V17c0 .55.47.98.97 1.21C16.15 18.75 17 20.24 17 22", "M18 2H6v7a6 6 0 0 0 12 0V2Z"],
  "crown": ["M11.562 3.266a.5.5 0 0 1 .876 0L15.39 8.87a1 1 0 0 0 1.516.294L21.183 5.5a.5.5 0 0 1 .798.519l-2.834 10.246a1 1 0 0 1-.956.734H5.81a1 1 0 0 1-.957-.734L2.02 6.02a.5.5 0 0 1 .798-.519l4.276 3.664a1 1 0 0 0 1.516-.294z", "M5 21h14"],
  "video": ["m16 13 5.223 3.482a.5.5 0 0 0 .777-.416V7.87a.5.5 0 0 0-.752-.432L16 10.5", "M4 6h10a2 2 0 0 1 2 2v8a2 2 0 0 1 -2 2h-10a2 2 0 0 1 -2 -2v-8a2 2 0 0 1 2 -2Z"],
  "camera": ["M14.5 4h-5L7 7H4a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2V9a2 2 0 0 0-2-2h-3l-2.5-3z", "M9 13a3 3 0 1 0 6 0a3 3 0 1 0 -6 0Z"],
  "mic": ["M12 2a3 3 0 0 0-3 3v7a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3Z", "M19 10v2a7 7 0 0 1-14 0v-2", "M12 19L12 22"],
  "music": ["M9 18V5l12-2v13", "M3 18a3 3 0 1 0 6 0a3 3 0 1 0 -6 0Z", "M15 16a3 3 0 1 0 6 0a3 3 0 1 0 -6 0Z"],
  "image": ["M5 3h14a2 2 0 0 1 2 2v14a2 2 0 0 1 -2 2h-14a2 2 0 0 1 -2 -2v-14a2 2 0 0 1 2 -2Z", "M7 9a2 2 0 1 0 4 0a2 2 0 1 0 -4 0Z", "m21 15-3.086-3.086a2 2 0 0 0-2.828 0L6 21"],
  "file-text": ["M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7Z", "M14 2v4a2 2 0 0 0 2 2h4", "M10 9H8", "M16 13H8", "M16 17H8"],
  "download": ["M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4", "M7 10L12 15L17 10", "M12 15L12 3"],
  "upload": ["M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4", "M17 8L12 3L7 8", "M12 3L12 15"],
  "share-2": ["M15 5a3 3 0 1 0 6 0a3 3 0 1 0 -6 0Z", "M3 12a3 3 0 1 0 6 0a3 3 0 1 0 -6 0Z", "M15 19a3 3 0 1 0 6 0a3 3 0 1 0 -6 0Z", "M8.59 13.51L15.42 17.49", "M15.41 6.51L8.59 10.49"],
  "bookmark": ["m19 21-7-4-7 4V5a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2v16z"],
  "plus": ["M5 12h14", "M12 5v14"],
  "minus": ["M5 12h14"],
  "arrow-right": ["M5 12h14", "m12 5 7 7-7 7"],
  "arrow-up-right": ["M7 7h10v10", "M7 17 17 7"],
  "arrow-down-right": ["m7 7 10 10", "M17 7v10H7"],
  "chevron-right": ["m9 18 6-6-6-6"],
  "refresh-cw": ["M3 12a9 9 0 0 1 9-9 9.75 9.75 0 0 1 6.74 2.74L21 8", "M21 3v5h-5", "M21 12a9 9 0 0 1-9 9 9.75 9.75 0 0 1-6.74-2.74L3 16", "M8 16H3v5"],
  "wifi": ["M12 20h.01", "M2 8.82a15 15 0 0 1 20 0", "M5 12.859a10 10 0 0 1 14 0", "M8.5 16.429a5 5 0 0 1 7 0"],
  "battery-full": ["M4 7h12a2 2 0 0 1 2 2v6a2 2 0 0 1 -2 2h-12a2 2 0 0 1 -2 -2v-6a2 2 0 0 1 2 -2Z", "M22 11L22 13", "M6 11L6 13", "M10 11L10 13", "M14 11L14 13"],
  "smartphone": ["M7 2h10a2 2 0 0 1 2 2v16a2 2 0 0 1 -2 2h-10a2 2 0 0 1 -2 -2v-16a2 2 0 0 1 2 -2Z", "M12 18h.01"],
  "monitor": ["M4 3h16a2 2 0 0 1 2 2v10a2 2 0 0 1 -2 2h-16a2 2 0 0 1 -2 -2v-10a2 2 0 0 1 2 -2Z", "M8 21L16 21", "M12 17L12 21"],
  "laptop": ["M20 16V7a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v9m16 0H4m16 0 1.28 2.55a1 1 0 0 1-.9 1.45H3.62a1 1 0 0 1-.9-1.45L4 16"],
  "mail": ["M4 4h16a2 2 0 0 1 2 2v12a2 2 0 0 1 -2 2h-16a2 2 0 0 1 -2 -2v-12a2 2 0 0 1 2 -2Z", "m22 7-8.97 5.7a1.94 1.94 0 0 1-2.06 0L2 7"],
  "at-sign": ["M8 12a4 4 0 1 0 8 0a4 4 0 1 0 -8 0Z", "M16 8v5a3 3 0 0 0 6 0v-1a10 10 0 1 0-4 8"],
  "hash": ["M4 9L20 9", "M4 15L20 15", "M10 3L8 21", "M16 3L14 21"],
  "lightbulb": ["M15 14c.2-1 .7-1.7 1.5-2.5 1-.9 1.5-2.2 1.5-3.5A6 6 0 0 0 6 8c0 1 .2 2.2 1.5 3.5.7.7 1.3 1.5 1.5 2.5", "M9 18h6", "M10 22h4"],
  "brain": ["M12 5a3 3 0 1 0-5.997.125 4 4 0 0 0-2.526 5.77 4 4 0 0 0 .556 6.588A4 4 0 1 0 12 18Z", "M12 5a3 3 0 1 1 5.997.125 4 4 0 0 1 2.526 5.77 4 4 0 0 1-.556 6.588A4 4 0 1 1 12 18Z", "M15 13a4.5 4.5 0 0 1-3-4 4.5 4.5 0 0 1-3 4", "M17.599 6.5a3 3 0 0 0 .399-1.375", "M6.003 5.125A3 3 0 0 0 6.401 6.5", "M3.477 10.896a4 4 0 0 1 .585-.396", "M19.938 10.5a4 4 0 0 1 .585.396", "M6 18a4 4 0 0 1-1.967-.516", "M19.967 17.484A4 4 0 0 1 18 18"],
  "bot": ["M12 8V4H8", "M6 8h12a2 2 0 0 1 2 2v8a2 2 0 0 1 -2 2h-12a2 2 0 0 1 -2 -2v-8a2 2 0 0 1 2 -2Z", "M2 14h2", "M20 14h2", "M15 13v2", "M9 13v2"],
  "cpu": ["M6 4h12a2 2 0 0 1 2 2v12a2 2 0 0 1 -2 2h-12a2 2 0 0 1 -2 -2v-12a2 2 0 0 1 2 -2Z", "M10 9h4a1 1 0 0 1 1 1v4a1 1 0 0 1 -1 1h-4a1 1 0 0 1 -1 -1v-4a1 1 0 0 1 1 -1Z", "M15 2v2", "M15 20v2", "M2 15h2", "M2 9h2", "M20 15h2", "M20 9h2", "M9 2v2", "M9 20v2"],
  "code": ["M16 18L22 12L16 6", "M8 6L2 12L8 18"],
  "terminal": ["M4 17L10 11L4 5", "M12 19L20 19"],
  "circle-help": ["M2 12a10 10 0 1 0 20 0a10 10 0 1 0 -20 0Z", "M9.09 9a3 3 0 0 1 5.83 1c0 2-3 3-3 3", "M12 17h.01"],
  "ban": ["M2 12a10 10 0 1 0 20 0a10 10 0 1 0 -20 0Z", "m4.9 4.9 14.2 14.2"],
  "skull": ["m12.5 17-.5-1-.5 1h1z", "M15 22a1 1 0 0 0 1-1v-1a2 2 0 0 0 1.56-3.25 8 8 0 1 0-11.12 0A2 2 0 0 0 8 20v1a1 1 0 0 0 1 1z", "M14 12a1 1 0 1 0 2 0a1 1 0 1 0 -2 0Z", "M8 12a1 1 0 1 0 2 0a1 1 0 1 0 -2 0Z"],
  "frown": ["M2 12a10 10 0 1 0 20 0a10 10 0 1 0 -20 0Z", "M16 16s-1.5-2-4-2-4 2-4 2", "M9 9L9.01 9", "M15 9L15.01 9"],
  "smile": ["M2 12a10 10 0 1 0 20 0a10 10 0 1 0 -20 0Z", "M8 14s1.5 2 4 2 4-2 4-2", "M9 9L9.01 9", "M15 9L15.01 9"],
  "coffee": ["M10 2v2", "M14 2v2", "M16 8a1 1 0 0 1 1 1v8a4 4 0 0 1-4 4H7a4 4 0 0 1-4-4V9a1 1 0 0 1 1-1h14a4 4 0 1 1 0 8h-1", "M6 2v2"],
  "moon": ["M12 3a6 6 0 0 0 9 9 9 9 0 1 1-9-9Z"],
  "sun": ["M8 12a4 4 0 1 0 8 0a4 4 0 1 0 -8 0Z", "M12 2v2", "M12 20v2", "m4.93 4.93 1.41 1.41", "m17.66 17.66 1.41 1.41", "M2 12h2", "M20 12h2", "m6.34 17.66-1.41 1.41", "m19.07 4.93-1.41 1.41"],
  "map-pin": ["M20 10c0 4.993-5.539 10.193-7.399 11.799a1 1 0 0 1-1.202 0C9.539 20.193 4 14.993 4 10a8 8 0 0 1 16 0", "M9 10a3 3 0 1 0 6 0a3 3 0 1 0 -6 0Z"],
  "list-checks": ["m3 17 2 2 4-4", "m3 7 2 2 4-4", "M13 6h8", "M13 12h8", "M13 18h8"],
  "layout-grid": ["M4 3h5a1 1 0 0 1 1 1v5a1 1 0 0 1 -1 1h-5a1 1 0 0 1 -1 -1v-5a1 1 0 0 1 1 -1Z", "M15 3h5a1 1 0 0 1 1 1v5a1 1 0 0 1 -1 1h-5a1 1 0 0 1 -1 -1v-5a1 1 0 0 1 1 -1Z", "M15 14h5a1 1 0 0 1 1 1v5a1 1 0 0 1 -1 1h-5a1 1 0 0 1 -1 -1v-5a1 1 0 0 1 1 -1Z", "M4 14h5a1 1 0 0 1 1 1v5a1 1 0 0 1 -1 1h-5a1 1 0 0 1 -1 -1v-5a1 1 0 0 1 1 -1Z"],
  "party-popper": ["M5.8 11.3 2 22l10.7-3.79", "M4 3h.01", "M22 8h.01", "M15 2h.01", "M22 20h.01", "m22 2-2.24.75a2.9 2.9 0 0 0-1.96 3.12c.1.86-.57 1.63-1.45 1.63h-.38c-.86 0-1.6.6-1.76 1.44L14 10", "m22 13-.82-.33c-.86-.34-1.82.2-1.98 1.11c-.11.7-.72 1.22-1.43 1.22H17", "m11 2 .33.82c.34.86-.2 1.82-1.11 1.98C9.52 4.9 9 5.52 9 6.23V7", "M11 13c1.93 1.93 2.83 4.17 2 5-.83.83-3.07-.07-5-2-1.93-1.93-2.83-4.17-2-5 .83-.83 3.07.07 5 2Z"],
  "badge-check": ["M3.85 8.62a4 4 0 0 1 4.78-4.77 4 4 0 0 1 6.74 0 4 4 0 0 1 4.78 4.78 4 4 0 0 1 0 6.74 4 4 0 0 1-4.77 4.78 4 4 0 0 1-6.75 0 4 4 0 0 1-4.78-4.77 4 4 0 0 1 0-6.76Z", "m9 12 2 2 4-4"],
  "megaphone": ["m3 11 18-5v12L3 14v-3z", "M11.6 16.8a3 3 0 1 1-5.8-1.6"],
  "percent": ["M19 5L5 19", "M4 6.5a2.5 2.5 0 1 0 5 0a2.5 2.5 0 1 0 -5 0Z", "M15 17.5a2.5 2.5 0 1 0 5 0a2.5 2.5 0 1 0 -5 0Z"],
  "coins": ["M2 8a6 6 0 1 0 12 0a6 6 0 1 0 -12 0Z", "M18.09 10.37A6 6 0 1 1 10.34 18", "M7 6h1v4", "m16.71 13.88.7.71-2.82 2.82"],
  "banknote": ["M4 6h16a2 2 0 0 1 2 2v8a2 2 0 0 1 -2 2h-16a2 2 0 0 1 -2 -2v-8a2 2 0 0 1 2 -2Z", "M10 12a2 2 0 1 0 4 0a2 2 0 1 0 -4 0Z", "M6 12h.01M18 12h.01"],
  "gamepad-2": ["M6 11L10 11", "M8 9L8 13", "M15 12L15.01 12", "M18 10L18.01 10", "M17.32 5H6.68a4 4 0 0 0-3.978 3.59c-.006.052-.01.101-.017.152C2.604 9.416 2 14.456 2 16a3 3 0 0 0 3 3c1 0 1.5-.5 2-1l1.414-1.414A2 2 0 0 1 9.828 16h4.344a2 2 0 0 1 1.414.586L17 18c.5.5 1 1 2 1a3 3 0 0 0 3-3c0-1.545-.604-6.584-.685-7.258-.007-.05-.011-.1-.017-.151A4 4 0 0 0 17.32 5z"],
  "headphones": ["M3 14h3a2 2 0 0 1 2 2v3a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-7a9 9 0 0 1 18 0v7a2 2 0 0 1-2 2h-1a2 2 0 0 1-2-2v-3a2 2 0 0 1 2-2h3"],
  "tv": ["M4 7h16a2 2 0 0 1 2 2v11a2 2 0 0 1 -2 2h-16a2 2 0 0 1 -2 -2v-11a2 2 0 0 1 2 -2Z", "M17 2L12 7L7 2"],
  "clapperboard": ["M20.2 6 3 11l-.9-2.4c-.3-1.1.3-2.2 1.3-2.5l13.5-4c1.1-.3 2.2.3 2.5 1.3Z", "m6.2 5.3 3.1 3.9", "m12.4 3.4 3.1 4", "M3 11h18v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2Z"],
  "film": ["M5 3h14a2 2 0 0 1 2 2v14a2 2 0 0 1 -2 2h-14a2 2 0 0 1 -2 -2v-14a2 2 0 0 1 2 -2Z", "M7 3v18", "M3 7.5h4", "M3 12h18", "M3 16.5h4", "M17 3v18", "M17 7.5h4", "M17 16.5h4"],
  "scissors": ["M3 6a3 3 0 1 0 6 0a3 3 0 1 0 -6 0Z", "M8.12 8.12 12 12", "M20 4 8.12 15.88", "M3 18a3 3 0 1 0 6 0a3 3 0 1 0 -6 0Z", "M14.8 14.8 20 20"],
  "wand-sparkles": ["m21.64 3.64-1.28-1.28a1.21 1.21 0 0 0-1.72 0L2.36 18.64a1.21 1.21 0 0 0 0 1.72l1.28 1.28a1.2 1.2 0 0 0 1.72 0L21.64 5.36a1.2 1.2 0 0 0 0-1.72", "m14 7 3 3", "M5 6v4", "M19 14v4", "M10 2v2", "M7 8H3", "M21 16h-4", "M11 3H9"],
  "pencil": ["M21.174 6.812a1 1 0 0 0-3.986-3.987L3.842 16.174a2 2 0 0 0-.5.83l-1.321 4.352a.5.5 0 0 0 .623.622l4.353-1.32a2 2 0 0 0 .83-.497z", "m15 5 4 4"],
  "trash-2": ["M3 6h18", "M19 6v14c0 1-1 2-2 2H7c-1 0-2-1-2-2V6", "M8 6V4c0-1 1-2 2-2h4c1 0 2 1 2 2v2", "M10 11L10 17", "M14 11L14 17"],
  "copy": ["M10 8h10a2 2 0 0 1 2 2v10a2 2 0 0 1 -2 2h-10a2 2 0 0 1 -2 -2v-10a2 2 0 0 1 2 -2Z", "M4 16c-1.1 0-2-.9-2-2V4c0-1.1.9-2 2-2h10c1.1 0 2 .9 2 2"],
  "external-link": ["M15 3h6v6", "M10 14 21 3", "M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"],
  "loader": ["M12 2v4", "m16.2 7.8 2.9-2.9", "M18 12h4", "m16.2 16.2 2.9 2.9", "M12 18v4", "m4.9 19.1 2.9-2.9", "M2 12h4", "m4.9 4.9 2.9 2.9"],
  "repeat": ["m17 2 4 4-4 4", "M3 11v-1a4 4 0 0 1 4-4h14", "m7 22-4-4 4-4", "M21 13v1a4 4 0 0 1-4 4H3"],
  "volume-2": ["M11 4.702a.705.705 0 0 0-1.203-.498L6.413 7.587A1.4 1.4 0 0 1 5.416 8H3a1 1 0 0 0-1 1v6a1 1 0 0 0 1 1h2.416a1.4 1.4 0 0 1 .997.413l3.383 3.384A.705.705 0 0 0 11 19.298z", "M16 9a5 5 0 0 1 0 6", "M19.364 18.364a9 9 0 0 0 0-12.728"],
  "circle-dot": ["M2 12a10 10 0 1 0 20 0a10 10 0 1 0 -20 0Z", "M11 12a1 1 0 1 0 2 0a1 1 0 1 0 -2 0Z"],
  "square-check": ["M5 3h14a2 2 0 0 1 2 2v14a2 2 0 0 1 -2 2h-14a2 2 0 0 1 -2 -2v-14a2 2 0 0 1 2 -2Z", "m9 12 2 2 4-4"],
  "square": ["M5 3h14a2 2 0 0 1 2 2v14a2 2 0 0 1 -2 2h-14a2 2 0 0 1 -2 -2v-14a2 2 0 0 1 2 -2Z"],
  "circle": ["M2 12a10 10 0 1 0 20 0a10 10 0 1 0 -20 0Z"],
};

/** Русские и короткие имена → имя Lucide (агенты пишут «галочка», «глаз», «warning»). */
export const ICON_ALIASES: Record<string, string> = {
  ok: "check", done: "check", "галочка": "check", "check-circle": "circle-check", "x-circle": "circle-x",
  warning: "triangle-alert", alert: "triangle-alert", "внимание": "triangle-alert", error: "circle-alert", "ошибка": "circle-alert",
  views: "eye", "глаз": "eye", "просмотры": "eye", like: "heart", "лайк": "heart", "сердце": "heart",
  comment: "message-circle", chat: "message-circle", "сообщение": "message-circle", time: "clock", "часы": "clock",
  "песочные часы": "hourglass", money: "dollar-sign", "деньги": "dollar-sign", "доллар": "dollar-sign", cash: "banknote",
  growth: "trending-up", "рост": "trending-up", "падение": "trending-down", chart: "chart-column", "график": "chart-column",
  home: "house", "дом": "house", fire: "flame", "огонь": "flame", bolt: "zap", "молния": "zap", "звезда": "star",
  "колокольчик": "bell", notification: "bell", "уведомление": "bell", "человек": "user", "люди": "users",
  "замок": "lock", shield: "shield-check", "щит": "shield-check", "ракета": "rocket", "цель": "target", "кубок": "trophy",
  "корона": "crown", "видео": "video", "камера": "camera", "микрофон": "mic", "музыка": "music", "картинка": "image",
  "файл": "file-text", "поиск": "search", "настройки": "settings", "подарок": "gift", "корзина": "shopping-cart",
  "карта": "credit-card", "кошелёк": "wallet", "мир": "globe", "ссылка": "link", "идея": "lightbulb", "мозг": "brain",
  robot: "bot", "робот": "bot", "ии": "sparkles", ai: "sparkles", "искры": "sparkles", "вопрос": "circle-help",
  question: "circle-help", "запрет": "ban", "телефон": "smartphone", phone: "smartphone", "монитор": "monitor",
  "ноутбук": "laptop", "почта": "mail", "кофе": "coffee", "календарь": "calendar", "таймер": "timer",
  "плей": "play", "список": "list-checks", "праздник": "party-popper", "рупор": "megaphone", "процент": "percent",
  "монеты": "coins", "геймпад": "gamepad-2", game: "gamepad-2", "наушники": "headphones", "фильм": "film",
  "монтаж": "scissors", "магия": "wand-sparkles", "карандаш": "pencil", "удалить": "trash-2", "звук": "volume-2",
};

/** Имя значка Lucide по имени или алиасу; неизвестное — null. */
export function flatIconName(name: string | null | undefined): string | null {
  if (!name) return null;
  const key = String(name).trim().toLowerCase();
  if (FLAT_ICONS[key]) return key;
  const alias = ICON_ALIASES[key];
  return alias && FLAT_ICONS[alias] ? alias : null;
}

export const FLAT_ICON_NAMES: readonly string[] = Object.keys(FLAT_ICONS);

/* ---------- пути SVG → команды Canvas 2D ---------- */

/**
 * Свой разбор путей SVG вместо Path2D: в Node (@napi-rs/canvas) Path2D не глобальный, а значки должны
 * рисоваться одинаково в превью, в рендере, в 3D-экранах и в тестах. Команды — абсолютные, дуги — центром
 * (ctx.ellipse), как в спецификации SVG (F.6.5).
 */
type Cmd = ["M" | "L", number, number] | ["C", number, number, number, number, number, number] | ["Q", number, number, number, number]
  | ["E", number, number, number, number, number, number, number, boolean] | ["Z"];

/** Разбор строки пути на команды и числа; флаги дуги — по одному символу («a1 1 0 011 1» — сжатая запись). */
function tokenize(d: string): (string | number)[] {
  const out: (string | number)[] = [];
  const NUM = /^[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?/;
  let i = 0;
  let cmd = "";
  let arg = 0; // номер числа внутри команды (у дуги — 7 на сегмент)
  while (i < d.length) {
    const ch = d[i];
    if (/[\s,]/.test(ch)) {
      i++;
      continue;
    }
    if (/[MmLlHhVvCcSsQqTtAaZz]/.test(ch)) {
      out.push(ch);
      cmd = ch;
      arg = 0;
      i++;
      continue;
    }
    if ((cmd === "a" || cmd === "A") && (arg % 7 === 3 || arg % 7 === 4) && (ch === "0" || ch === "1")) {
      out.push(Number(ch));
      arg++;
      i++;
      continue;
    }
    const m = NUM.exec(d.slice(i));
    if (!m) {
      i++;
      continue;
    }
    out.push(Number(m[0]));
    arg++;
    i += m[0].length;
  }
  return out;
}

function arcToCenter(x1: number, y1: number, rx: number, ry: number, phiDeg: number, fa: number, fs: number, x2: number, y2: number): Cmd | null {
  if (rx === 0 || ry === 0) return ["L", x2, y2];
  const phi = (phiDeg * Math.PI) / 180;
  const cos = Math.cos(phi);
  const sin = Math.sin(phi);
  const dx = (x1 - x2) / 2;
  const dy = (y1 - y2) / 2;
  const x1p = cos * dx + sin * dy;
  const y1p = -sin * dx + cos * dy;
  rx = Math.abs(rx);
  ry = Math.abs(ry);
  const lam = (x1p * x1p) / (rx * rx) + (y1p * y1p) / (ry * ry);
  if (lam > 1) {
    rx *= Math.sqrt(lam);
    ry *= Math.sqrt(lam);
  }
  const num = rx * rx * ry * ry - rx * rx * y1p * y1p - ry * ry * x1p * x1p;
  const den = rx * rx * y1p * y1p + ry * ry * x1p * x1p;
  let co = Math.sqrt(Math.max(0, num / den));
  if (fa === fs) co = -co;
  const cxp = (co * rx * y1p) / ry;
  const cyp = (-co * ry * x1p) / rx;
  const cx = cos * cxp - sin * cyp + (x1 + x2) / 2;
  const cy = sin * cxp + cos * cyp + (y1 + y2) / 2;
  const ang = (ux: number, uy: number, vx: number, vy: number) => {
    const a = Math.atan2(ux * vy - uy * vx, ux * vx + uy * vy);
    return a;
  };
  const a1 = ang(1, 0, (x1p - cxp) / rx, (y1p - cyp) / ry);
  let da = ang((x1p - cxp) / rx, (y1p - cyp) / ry, (-x1p - cxp) / rx, (-y1p - cyp) / ry);
  if (!fs && da > 0) da -= Math.PI * 2;
  if (fs && da < 0) da += Math.PI * 2;
  return ["E", cx, cy, rx, ry, phi, a1, a1 + da, !fs];
}

/** Путь SVG → абсолютные команды (M, L, C, Q, дуга E, Z). */
export function parseSvgPath(d: string): Cmd[] {
  const t = tokenize(d);
  const out: Cmd[] = [];
  let i = 0;
  let cmd = "";
  let x = 0;
  let y = 0;
  let sx = 0;
  let sy = 0;
  let lcx = 0; // последняя контрольная точка (S/T)
  let lcy = 0;
  let prev = "";
  const num = () => Number(t[i++]);
  while (i < t.length) {
    if (typeof t[i] === "string") cmd = t[i++] as string;
    else if (cmd === "M") cmd = "L";
    else if (cmd === "m") cmd = "l";
    const rel = cmd === cmd.toLowerCase();
    const C = cmd.toUpperCase();
    const ox = rel ? x : 0;
    const oy = rel ? y : 0;
    switch (C) {
      case "M":
        x = ox + num();
        y = oy + num();
        sx = x;
        sy = y;
        out.push(["M", x, y]);
        break;
      case "L":
        x = ox + num();
        y = oy + num();
        out.push(["L", x, y]);
        break;
      case "H":
        x = (rel ? x : 0) + num();
        out.push(["L", x, y]);
        break;
      case "V":
        y = (rel ? y : 0) + num();
        out.push(["L", x, y]);
        break;
      case "C": {
        const c1x = ox + num(), c1y = oy + num(), c2x = ox + num(), c2y = oy + num();
        x = ox + num();
        y = oy + num();
        out.push(["C", c1x, c1y, c2x, c2y, x, y]);
        lcx = c2x;
        lcy = c2y;
        break;
      }
      case "S": {
        const c1x = prev === "C" || prev === "S" ? 2 * x - lcx : x;
        const c1y = prev === "C" || prev === "S" ? 2 * y - lcy : y;
        const c2x = ox + num(), c2y = oy + num();
        x = ox + num();
        y = oy + num();
        out.push(["C", c1x, c1y, c2x, c2y, x, y]);
        lcx = c2x;
        lcy = c2y;
        break;
      }
      case "Q": {
        const qx = ox + num(), qy = oy + num();
        x = ox + num();
        y = oy + num();
        out.push(["Q", qx, qy, x, y]);
        lcx = qx;
        lcy = qy;
        break;
      }
      case "T": {
        const qx = prev === "Q" || prev === "T" ? 2 * x - lcx : x;
        const qy = prev === "Q" || prev === "T" ? 2 * y - lcy : y;
        x = ox + num();
        y = oy + num();
        out.push(["Q", qx, qy, x, y]);
        lcx = qx;
        lcy = qy;
        break;
      }
      case "A": {
        const rx = num(), ry = num(), rot = num(), fa = num(), fs = num();
        const nx = ox + num(), ny = oy + num();
        const arc = arcToCenter(x, y, rx, ry, rot, fa, fs, nx, ny);
        if (arc) out.push(arc);
        x = nx;
        y = ny;
        break;
      }
      case "Z":
        out.push(["Z"]);
        x = sx;
        y = sy;
        break;
      default:
        i++;
    }
    prev = C;
  }
  return out;
}

type PathCtx = {
  beginPath(): void;
  moveTo(x: number, y: number): void;
  lineTo(x: number, y: number): void;
  bezierCurveTo(a: number, b: number, c: number, d: number, e: number, f: number): void;
  quadraticCurveTo(a: number, b: number, c: number, d: number): void;
  ellipse(x: number, y: number, rx: number, ry: number, rot: number, a1: number, a2: number, ccw?: boolean): void;
  closePath(): void;
};

/** Проложить команды в текущий путь контекста (без beginPath). */
export function tracePath(ctx: PathCtx, cmds: Cmd[]): void {
  for (const c of cmds) {
    if (c[0] === "M") ctx.moveTo(c[1], c[2]);
    else if (c[0] === "L") ctx.lineTo(c[1], c[2]);
    else if (c[0] === "C") ctx.bezierCurveTo(c[1], c[2], c[3], c[4], c[5], c[6]);
    else if (c[0] === "Q") ctx.quadraticCurveTo(c[1], c[2], c[3], c[4]);
    else if (c[0] === "E") ctx.ellipse(c[1], c[2], c[3], c[4], c[5], c[6], c[7], c[8]);
    else ctx.closePath();
  }
}

const cmdCache = new Map<string, Cmd[][]>();

function iconCmds(name: string): Cmd[][] {
  let hit = cmdCache.get(name);
  if (!hit) {
    hit = (FLAT_ICONS[name] || []).map(parseSvgPath);
    cmdCache.set(name, hit);
  }
  return hit;
}

type StrokeCtx = PathCtx & {
  save(): void;
  restore(): void;
  translate(x: number, y: number): void;
  scale(x: number, y: number): void;
  stroke(): void;
  lineWidth: number;
  lineCap: CanvasLineCap;
  lineJoin: CanvasLineJoin;
  strokeStyle: string | CanvasGradient | CanvasPattern;
};

/**
 * Нарисовать значок обводкой: центр (cx, cy), размер `size` (сторона сетки 24), цвет, толщина в px сетки
 * (2 — как в наборе). Неизвестное имя — ничего не рисует и возвращает false.
 */
export function drawFlatIcon(ctx: StrokeCtx, name: string | null | undefined, cx: number, cy: number, size: number, color: string, stroke = 2): boolean {
  const key = flatIconName(name);
  if (!key) return false;
  const k = size / 24;
  ctx.save();
  ctx.translate(cx - size / 2, cy - size / 2);
  ctx.scale(k, k);
  ctx.lineWidth = stroke;
  ctx.lineCap = "round";
  ctx.lineJoin = "round";
  ctx.strokeStyle = color;
  for (const cmds of iconCmds(key)) {
    ctx.beginPath();
    tracePath(ctx, cmds);
    ctx.stroke();
  }
  ctx.restore();
  return true;
}
