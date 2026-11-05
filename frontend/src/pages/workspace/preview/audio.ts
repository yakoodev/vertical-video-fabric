/**
 * Звук предпросмотра монтажа: граф WebAudio в такт времени готового ролика.
 *
 * Звук исходника → шина с тишиной, громкостью отрезков, ключами и простыми эффектами (громче 1
 * теперь можно: тег <video> сам не умел), музыка с петлёй, фейдами, автоматизацией и ducking,
 * звуки слоёв и стыков — точные AudioBuffer (не теги <audio>, которые запаздывают на старте).
 *
 * Громкость ролика до −14 LUFS рендер выставляет в конце: здесь её нет, это в списке расхождений.
 */
import type { PlaybackPlan } from "@/api/playback";
import { gainFactorAt, jlSourceAt, keyDbAt, musicFactorAt, muteFactorAt } from "./timeline";

/** Как app/motion.track_gain: volume × gain_db, не громче +12 дБ. */
function trackFactor(volume: number, gainDb: number): number {
  const v = Math.min(2, Math.max(0, Number.isFinite(volume) ? volume : 1));
  const g = Math.min(12, Math.max(-30, gainDb || 0));
  return Math.min(v * 10 ** (g / 20), 10 ** (12 / 20));
}

type Running = { node: AudioBufferSourceNode; gain: GainNode };

export class MontageAudio {
  readonly ctx: AudioContext;
  private master: GainNode;
  private sourceBus: GainNode;
  private fxInput: GainNode;
  private lowpass: BiquadFilterNode;
  private highpass: BiquadFilterNode;
  private delay: DelayNode;
  private delayGain: GainNode;
  private feedback: GainNode;
  private beep: OscillatorNode | null = null;
  private beepGain: GainNode;
  private noiseGain: GainNode;
  private sourceMeter: AnalyserNode;
  private masterMeter: AnalyserNode;
  private meterBuf = new Float32Array(1024);
  private musicEl: HTMLAudioElement;
  private musicGain: GainNode;
  private musicUrl = "";
  private sideEl: HTMLAudioElement;
  private sideGain: GainNode;
  private videoGains = new Map<HTMLMediaElement, GainNode>();
  private buffers = new Map<string, AudioBuffer | "loading" | "failed">();
  private running = new Map<string, Running>();
  private finished = new Set<string>();
  private reduction = 1;
  private lastTick = 0;

  constructor() {
    const Ctor = window.AudioContext ?? (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext;
    this.ctx = new Ctor();
    const c = this.ctx;
    this.master = c.createGain();
    this.masterMeter = c.createAnalyser();
    this.masterMeter.fftSize = 1024;
    this.master.connect(this.masterMeter);
    this.masterMeter.connect(c.destination);
    // Исходник: элементы → fxInput → (срез частот) → sourceBus → master; эхо — параллельной веткой.
    this.fxInput = c.createGain();
    this.highpass = c.createBiquadFilter();
    this.highpass.type = "highpass";
    this.highpass.frequency.value = 10;
    this.lowpass = c.createBiquadFilter();
    this.lowpass.type = "lowpass";
    this.lowpass.frequency.value = 22000;
    this.sourceBus = c.createGain();
    this.sourceMeter = c.createAnalyser();
    this.sourceMeter.fftSize = 1024;
    this.fxInput.connect(this.highpass);
    this.highpass.connect(this.lowpass);
    this.lowpass.connect(this.sourceBus);
    this.sourceBus.connect(this.sourceMeter);
    this.sourceMeter.connect(this.master);
    this.delay = c.createDelay(1.5);
    this.delay.delayTime.value = 0.3;
    this.delayGain = c.createGain();
    this.delayGain.gain.value = 0;
    this.feedback = c.createGain();
    this.feedback.gain.value = 0;
    this.lowpass.connect(this.delay);
    this.delay.connect(this.delayGain);
    this.delayGain.connect(this.sourceBus);
    this.delay.connect(this.feedback);
    this.feedback.connect(this.delay);
    this.beepGain = c.createGain();
    this.beepGain.gain.value = 0;
    this.beepGain.connect(this.master);
    this.noiseGain = c.createGain();
    this.noiseGain.gain.value = 0;
    this.noiseGain.connect(this.master);
    this.musicEl = new Audio();
    this.musicEl.preload = "auto";
    this.musicGain = c.createGain();
    this.musicGain.gain.value = 0;
    c.createMediaElementSource(this.musicEl).connect(this.musicGain);
    this.musicGain.connect(this.master);
    this.sideEl = new Audio();
    this.sideEl.preload = "auto";
    this.sideGain = c.createGain();
    this.sideGain.gain.value = 0;
    c.createMediaElementSource(this.sideEl).connect(this.sideGain);
    this.sideGain.connect(this.fxInput);
  }

  /** Подключить <video> основы; возвращает его личную громкость (наплыв, тишина куска). */
  connectVideo(el: HTMLMediaElement): void {
    if (this.videoGains.has(el)) return;
    const gain = this.ctx.createGain();
    gain.gain.value = 0;
    this.ctx.createMediaElementSource(el).connect(gain);
    gain.connect(this.fxInput);
    this.videoGains.set(el, gain);
  }

  setVideoLevel(el: HTMLMediaElement, value: number, ramp = 0.008): void {
    const gain = this.videoGains.get(el);
    if (gain) gain.gain.setTargetAtTime(value, this.ctx.currentTime, ramp);
  }

  setOutput(volume: number, muted: boolean): void {
    this.master.gain.setTargetAtTime(muted ? 0 : volume, this.ctx.currentTime, 0.01);
  }

  resume(): void {
    if (this.ctx.state === "suspended") void this.ctx.resume();
  }

  /** Громкость на выходе, для проверок и индикатора (0..1, RMS). */
  outputLevel(): number {
    return this.rms(this.masterMeter);
  }

  private rms(node: AnalyserNode): number {
    node.getFloatTimeDomainData(this.meterBuf);
    let sum = 0;
    for (let i = 0; i < this.meterBuf.length; i++) sum += this.meterBuf[i] * this.meterBuf[i];
    return Math.sqrt(sum / this.meterBuf.length);
  }

  /** Заранее скачать короткие звуки плана: первый звук не должен опоздать. */
  preload(plan: PlaybackPlan): void {
    for (const s of plan.audio.sfx) this.loadBuffer(s.url);
    for (const s of plan.audio.join_sfx) this.loadBuffer(s.url);
    const music = plan.audio.music;
    if (music && music.url !== this.musicUrl) {
      this.musicUrl = music.url;
      this.musicEl.src = music.url;
    }
  }

  private loadBuffer(url: string): void {
    if (this.buffers.has(url)) return;
    this.buffers.set(url, "loading");
    void fetch(url, { credentials: "same-origin" })
      .then((r) => (r.ok ? r.arrayBuffer() : Promise.reject(new Error(String(r.status)))))
      .then((data) => this.ctx.decodeAudioData(data))
      .then((buffer) => this.buffers.set(url, buffer))
      .catch(() => this.buffers.set(url, "failed"));
  }

  private startBuffer(key: string, url: string, offset: number, level: number, stopAfter: number | null): void {
    const buffer = this.buffers.get(url);
    if (!buffer || typeof buffer === "string" || this.running.has(key) || this.finished.has(key)) return;
    if (offset >= buffer.duration) {
      this.finished.add(key);
      return;
    }
    const node = this.ctx.createBufferSource();
    node.buffer = buffer;
    const gain = this.ctx.createGain();
    gain.gain.value = level;
    node.connect(gain);
    gain.connect(this.master);
    node.onended = () => {
      if (this.running.get(key)?.node === node) {
        this.running.delete(key);
        this.finished.add(key);
      }
    };
    if (stopAfter != null) node.start(0, Math.max(0, offset), Math.max(0.001, stopAfter));
    else node.start(0, Math.max(0, offset));
    this.running.set(key, { node, gain });
  }

  /** Стоп всех звуков: пауза или перемотка. */
  stopAll(): void {
    for (const r of this.running.values()) {
      r.node.onended = null;
      try {
        r.node.stop();
      } catch {
        /* уже доиграл */
      }
      r.gain.disconnect();
    }
    this.running.clear();
    this.finished.clear();
    if (!this.musicEl.paused) this.musicEl.pause();
    if (!this.sideEl.paused) this.sideEl.pause();
    this.musicGain.gain.setTargetAtTime(0, this.ctx.currentTime, 0.01);
    this.beepGain.gain.setTargetAtTime(0, this.ctx.currentTime, 0.01);
    this.noiseGain.gain.setTargetAtTime(0, this.ctx.currentTime, 0.01);
    this.sideGain.gain.setTargetAtTime(0, this.ctx.currentTime, 0.01);
  }

  private ensureBeep(): void {
    if (this.beep) return;
    this.beep = this.ctx.createOscillator();
    this.beep.frequency.value = 1000;
    this.beep.connect(this.beepGain);
    this.beep.start();
    const noise = this.ctx.createBufferSource();
    const buf = this.ctx.createBuffer(1, this.ctx.sampleRate, this.ctx.sampleRate);
    const ch = buf.getChannelData(0);
    for (let i = 0; i < ch.length; i++) ch[i] = Math.random() * 2 - 1;
    noise.buffer = buf;
    noise.loop = true;
    noise.connect(this.noiseGain);
    noise.start();
  }

  /** Каждый кадр анимации, пока играет: громкости, музыка, звуки — в момент `t`. */
  tick(plan: PlaybackPlan, t: number, opts: { inserted: boolean; sideUrl: string | null; insertDuck: number }): void {
    const now = this.ctx.currentTime;
    const audio = plan.audio;
    const finalEnd = plan.duration - plan.hold;
    let source = muteFactorAt(audio.mutes, t) * gainFactorAt(audio.gains, t) * 10 ** (keyDbAt(audio.source_keys, t) / 20) * opts.insertDuck;
    if (opts.inserted || t >= finalEnd) source = 0;
    // Эффекты на отрезке: простые — графом, остальное честно в warnings плана.
    let lp = 22000;
    let hp = 10;
    let echo = 0;
    let beep = 0;
    let noise = 0;
    for (const fx of audio.fx) {
      if (t < fx.start || t >= fx.end) continue;
      const a = Math.min(1, Math.max(0, fx.amount ?? 0.5));
      if (fx.fx === "muffle") lp = 1400 - 1000 * a;
      else if (fx.fx === "underwater") lp = 700 - 400 * a;
      else if (fx.fx === "phone") { hp = 380; lp = 3200; }
      else if (fx.fx === "radio") { hp = 600; lp = 2400; }
      else if (fx.fx === "echo" || fx.fx === "reverb") echo = 0.35 + 0.25 * a;
      else if (fx.fx === "beep") { beep = 0.25; source = 0; }
      else if (fx.fx === "noise") { noise = 0.15; source = 0; }
    }
    this.sourceBus.gain.setTargetAtTime(source, now, 0.008);
    this.lowpass.frequency.setTargetAtTime(lp, now, 0.01);
    this.highpass.frequency.setTargetAtTime(hp, now, 0.01);
    this.delayGain.gain.setTargetAtTime(echo, now, 0.02);
    this.feedback.gain.setTargetAtTime(echo * 0.6, now, 0.02);
    if (beep || noise) this.ensureBeep();
    this.beepGain.gain.setTargetAtTime(beep, now, 0.005);
    this.noiseGain.gain.setTargetAtTime(noise, now, 0.005);

    // J/L: звук соседнего куска идёт вторым плеером того же файла.
    const side = jlSourceAt(audio.jl, t);
    if (side != null && opts.sideUrl) {
      if (this.sideEl.getAttribute("src") !== opts.sideUrl) this.sideEl.src = opts.sideUrl;
      if (Math.abs(this.sideEl.currentTime - side) > 0.12) this.sideEl.currentTime = side;
      if (this.sideEl.paused) void this.sideEl.play().catch(() => undefined);
      this.sideGain.gain.setTargetAtTime(1, now, 0.01);
    } else if (!this.sideEl.paused) {
      this.sideEl.pause();
      this.sideGain.gain.setTargetAtTime(0, now, 0.01);
    }

    // Ducking музыки по настоящему звуку исходника: тот же порог и степень, что sidechaincompress.
    const music = audio.music;
    if (music) {
      const dt = Math.min(0.1, Math.max(0, now - this.lastTick));
      let target = 1;
      if (music.duck) {
        const level = this.rms(this.sourceMeter);
        if (level > 0.1) {
          const ratio = 1.5 + Math.min(1, Math.max(0, music.duck_amount)) * 3.5;
          target = (level / 0.1) ** (1 / ratio - 1);
        }
      }
      const tc = target < this.reduction ? 0.02 : 0.4; // attack=20 мс, release=400 мс
      this.reduction += (target - this.reduction) * (1 - Math.exp(-dt / tc));
      const base = music.fixed_db != null ? 10 ** (Math.min(12, Math.max(-40, music.fixed_db)) / 20) : music.volume;
      let fade = 1;
      if (music.fade_in > 0) fade *= Math.min(1, t / music.fade_in);
      if (music.fade_out > 0) fade *= Math.min(1, Math.max(0, (plan.duration - t) / music.fade_out));
      this.musicGain.gain.setTargetAtTime(base * fade * musicFactorAt(music.automation, t) * this.reduction, now, 0.015);
      const dur = Number.isFinite(this.musicEl.duration) ? this.musicEl.duration : 0;
      let pos = music.start_sec + t;
      let audible = true;
      if (dur > 0 && pos >= dur) {
        if (music.loop) pos = pos % dur;
        else audible = false;
      }
      if (audible) {
        if (Math.abs(this.musicEl.currentTime - pos) > 0.25) this.musicEl.currentTime = pos;
        if (this.musicEl.paused) void this.musicEl.play().catch(() => undefined);
      } else if (!this.musicEl.paused) this.musicEl.pause();
    } else if (!this.musicEl.paused) {
      this.musicEl.pause();
    }
    this.lastTick = now;

    // Звуки слоёв и стыков: вошли в отрезок — стартуем с нужного места.
    for (const s of audio.sfx) {
      const key = `s:${s.id}:${s.start}`;
      if (t >= s.start && t < s.end) {
        const level = s.track ? trackFactor(s.volume, s.gain_db) : Math.min(1, Math.max(0, s.volume));
        this.startBuffer(key, s.url, s.offset + (t - s.start), level, s.end - t);
      }
    }
    for (const j of audio.join_sfx) {
      const key = `j:${j.at}`;
      if (t >= j.at && t < j.at + 0.2) this.startBuffer(key, j.url, t - j.at, j.volume, null);
    }
  }

  destroy(): void {
    this.stopAll();
    this.musicEl.removeAttribute("src");
    this.sideEl.removeAttribute("src");
    if (this.beep) this.beep.stop();
    void this.ctx.close();
  }
}
