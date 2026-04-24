(() => {
  const STORAGE_KEY = "chanakya-voice-avatar-character";
  const CHARACTER_LABELS = {
    eyes: "Eyes-Driven",
    mythic: "Mythic Indian",
    character: "Character",
    komi: "Komi Live2D",
  };

  const CHARACTER_SUBTITLES = {
    eyes: "Expressive gaze tracking",
    mythic: "Mandala-driven presence",
    character: "Animated companion",
    komi: "Live2D stage performance",
  };

  const STAGE_STATE_MAP = {
    idle: "idle",
    listening: "listening",
    thinking: "thinking",
    speaking: "speaking",
    queued: "speaking",
    error: "error",
  };

  const KOMI_LIVE2D_CONFIG = {
    modelPath: "/static/assets/komi/Komi.model3.json",
    stateToMotion: {
      idle: "Idle",
      listening: "Curious",
      thinking: "Shy",
      speaking: "Happy",
      error: "Menacing",
    },
    stateToExpression: {
      idle: "mouth",
      listening: "cat-ears",
      thinking: "pout",
      speaking: "mouth",
      error: "nose",
    },
    idleExpressionCycle: {
      intervalMs: 3200,
      options: [
        { name: "mouth", weight: 8 },
        { name: "blush", weight: 2 },
        { name: "eye-shine", weight: 2 },
        { name: "pout", weight: 1 },
        { name: "cat-ears", weight: 1 },
      ],
    },
    layout: {
      widthRatio: 0.76,
      heightRatio: 0.9,
      bottomOffsetRatio: 0.015,
    },
  };

  class KomiLive2DController {
    constructor(options) {
      this.canvas = options.canvas;
      this.container = options.container;
      this.loadingNode = options.loadingNode;
      this.config = options.config;
      this.currentState = "idle";
      this.currentExpression = null;
      this.currentMotionGroup = null;
      this.initPromise = null;
      this.isInitialized = false;
      this.app = null;
      this.model = null;
      this.idleExpressionTimer = null;
      this.resizeObserver = null;
      this.boundResize = () => this.layoutModel();
    }

    async init() {
      if (this.isInitialized) {
        return;
      }
      if (this.initPromise) {
        return this.initPromise;
      }
      this.initPromise = this.loadModel();
      return this.initPromise;
    }

    async loadModel() {
      const pixi = window.PIXI;
      const live2d = pixi && pixi.live2d;
      if (!this.canvas || !this.container || !pixi || !live2d || !live2d.Live2DModel) {
        this.fail("Live2D runtime unavailable");
        this.initPromise = null;
        return;
      }

      try {
        this.setLoading("Loading Komi...");
        this.app = new pixi.Application({
          view: this.canvas,
          autoStart: true,
          autoDensity: true,
          antialias: true,
          backgroundAlpha: 0,
          resizeTo: this.container,
        });
        this.model = await live2d.Live2DModel.from(this.config.modelPath, {
          autoInteract: false,
        });
        this.app.stage.addChild(this.model);
        this.canvas.classList.add("is-ready");
        this.observeResize();
        this.layoutModel();
        this.isInitialized = true;
        this.hideLoading();
        await this.setState(this.currentState, { force: true });
      } catch (error) {
        console.error("Failed to initialize Komi Live2D", error);
        this.fail("Komi failed to load");
      } finally {
        this.initPromise = null;
      }
    }

    observeResize() {
      window.addEventListener("resize", this.boundResize, { passive: true });
      if (window.ResizeObserver && this.container) {
        this.resizeObserver = new ResizeObserver(() => this.layoutModel());
        this.resizeObserver.observe(this.container);
      }
    }

    layoutModel() {
      if (!this.model || !this.container) {
        return;
      }
      const width = this.container.clientWidth;
      const height = this.container.clientHeight;
      if (!width || !height) {
        return;
      }
      const bounds = this.model.getLocalBounds();
      const safeWidth = Math.max(bounds.width, 1);
      const safeHeight = Math.max(bounds.height, 1);
      const scale = Math.min(
        (width * this.config.layout.widthRatio) / safeWidth,
        (height * this.config.layout.heightRatio) / safeHeight,
      );
      this.model.scale.set(scale);
      const scaledWidth = safeWidth * scale;
      const scaledHeight = safeHeight * scale;
      const x = (width - scaledWidth) / 2 - bounds.x * scale;
      const y = height - scaledHeight - bounds.y * scale - height * this.config.layout.bottomOffsetRatio;
      this.model.position.set(x, y);
    }

    async setState(state, options = {}) {
      this.currentState = state;
      if (!this.isInitialized) {
        await this.init();
      }
      if (!this.isInitialized) {
        return;
      }
      this.playMotionForState(state, options.force === true);
      this.applyExpressionForState(state);
      if (state === "idle") {
        this.startIdleExpressionCycle();
      } else {
        this.stopIdleExpressionCycle();
      }
    }

    playMotionForState(state, force = false) {
      const group = this.config.stateToMotion[state] || null;
      if (!group || (!force && this.currentMotionGroup === group) || !this.model) {
        return;
      }
      this.currentMotionGroup = group;
      try {
        const priority = window.PIXI.live2d.MotionPriority && window.PIXI.live2d.MotionPriority.NORMAL;
        this.model.motion(group, undefined, priority);
      } catch (error) {
        console.warn(`Unable to play motion group ${group}`, error);
      }
    }

    applyExpressionForState(state) {
      this.applyExpression(this.config.stateToExpression[state] || null);
    }

    applyExpression(expression) {
      if (!this.model || typeof this.model.expression !== "function" || expression === this.currentExpression) {
        return;
      }
      this.currentExpression = expression;
      try {
        if (expression) {
          this.model.expression(expression);
        }
      } catch (error) {
        console.warn(`Unable to apply expression ${expression}`, error);
      }
    }

    startIdleExpressionCycle() {
      if (!this.config.idleExpressionCycle || this.idleExpressionTimer) {
        return;
      }
      this.idleExpressionTimer = window.setInterval(() => {
        if (this.currentState !== "idle") {
          return;
        }
        const next = this.pickWeightedOption(this.config.idleExpressionCycle.options);
        if (next) {
          this.applyExpression(next);
        }
      }, this.config.idleExpressionCycle.intervalMs);
    }

    stopIdleExpressionCycle() {
      if (!this.idleExpressionTimer) {
        return;
      }
      window.clearInterval(this.idleExpressionTimer);
      this.idleExpressionTimer = null;
    }

    pickWeightedOption(options = []) {
      if (!options.length) {
        return null;
      }
      const totalWeight = options.reduce((sum, option) => sum + Math.max(option.weight || 0, 0), 0);
      if (totalWeight <= 0) {
        return options[0].name || null;
      }
      let threshold = Math.random() * totalWeight;
      for (const option of options) {
        threshold -= Math.max(option.weight || 0, 0);
        if (threshold <= 0) {
          return option.name || null;
        }
      }
      return options[0].name || null;
    }

    setLoading(message) {
      if (this.loadingNode) {
        this.loadingNode.textContent = message;
        this.loadingNode.classList.remove("hidden");
      }
    }

    hideLoading() {
      if (this.loadingNode) {
        this.loadingNode.classList.add("hidden");
      }
    }

    fail(message) {
      if (this.loadingNode) {
        this.loadingNode.textContent = message;
        this.loadingNode.classList.remove("hidden");
      }
    }
  }

  class VoiceAvatarStage {
    constructor(options) {
      this.stage = options.stage;
      this.nameNode = options.nameNode;
      this.shell = this.stage.querySelector(".voice-avatar-shell");
      this.backgroundParticles = this.stage.querySelector(".voice-avatar-bg-particles");
      this.particleCanvas = this.stage.querySelector(".voice-avatar-particle-canvas");
      this.chooserButton = this.stage.querySelector("#voiceAvatarChooserButton");
      this.chooserMenu = this.stage.querySelector("#voiceAvatarChooserMenu");
      this.optionButtons = Array.from(this.stage.querySelectorAll(".voice-avatar-option"));
      this.eyeNodes = Array.from(this.stage.querySelectorAll(".voice-eyes-eye"));
      this.characterEyeNodes = Array.from(this.stage.querySelectorAll(".voice-character-eye"));
      this.currentCharacter = "eyes";
      this.currentState = "idle";
      this.blinkTimer = null;
      this.particles = [];
      this.particleCtx = null;
      this.komi = new KomiLive2DController({
        canvas: this.stage.querySelector("#voiceAvatarKomiCanvas"),
        container: this.stage.querySelector(".voice-komi-canvas-shell"),
        loadingNode: this.stage.querySelector("#voiceAvatarKomiLoading"),
        config: KOMI_LIVE2D_CONFIG,
      });
      this.handleDocumentClick = this.handleDocumentClick.bind(this);
      this.handlePointerMove = this.handlePointerMove.bind(this);
      this.handleResize = this.handleResize.bind(this);
      this.init();
    }

    init() {
      this.populateBackgroundParticles();
      this.initParticles();
      this.initChooser();
      this.initPointerTracking();
      this.startBlinkLoop();
      const saved = window.localStorage.getItem(STORAGE_KEY);
      this.setCharacter(saved && CHARACTER_LABELS[saved] ? saved : "eyes");
      this.setState("idle");
    }

    initChooser() {
      if (this.chooserButton) {
        this.chooserButton.addEventListener("click", () => {
          const expanded = this.chooserButton.getAttribute("aria-expanded") === "true";
          this.setChooserOpen(!expanded);
        });
      }
      this.optionButtons.forEach((button) => {
        button.addEventListener("click", () => {
          this.setCharacter(button.dataset.character || "eyes");
          this.setChooserOpen(false);
        });
      });
      document.addEventListener("click", this.handleDocumentClick);
      document.addEventListener("keydown", (event) => {
        if (event.key === "Escape") {
          this.setChooserOpen(false);
        }
      });
    }

    initPointerTracking() {
      document.addEventListener("mousemove", this.handlePointerMove, { passive: true });
    }

    initParticles() {
      if (!this.particleCanvas) {
        return;
      }
      this.particleCtx = this.particleCanvas.getContext("2d");
      this.resizeParticleCanvas();
      window.addEventListener("resize", this.handleResize, { passive: true });
      this.animateParticles();
    }

    handleResize() {
      this.resizeParticleCanvas();
    }

    resizeParticleCanvas() {
      if (!this.particleCanvas || !this.particleCtx || !this.shell) {
        return;
      }
      const size = Math.min(this.shell.clientWidth, this.shell.clientHeight);
      const pixelRatio = Math.min(window.devicePixelRatio || 1, 2);
      this.particleCanvas.width = size * pixelRatio;
      this.particleCanvas.height = size * pixelRatio;
      this.particleCanvas.style.width = `${size}px`;
      this.particleCanvas.style.height = `${size}px`;
      this.particleCtx.setTransform(pixelRatio, 0, 0, pixelRatio, 0, 0);
    }

    populateBackgroundParticles() {
      if (!this.backgroundParticles) {
        return;
      }
      this.backgroundParticles.innerHTML = "";
      for (let index = 0; index < 24; index += 1) {
        const particle = document.createElement("span");
        particle.style.setProperty("--size", `${Math.random() * 3 + 1}px`);
        particle.style.setProperty("--left", `${Math.random() * 100}%`);
        particle.style.setProperty("--top", `${Math.random() * 100}%`);
        particle.style.setProperty("--delay", `${Math.random() * 6}s`);
        particle.style.setProperty("--duration", `${Math.random() * 10 + 10}s`);
        this.backgroundParticles.appendChild(particle);
      }
    }

    createParticles(count, color) {
      if (!this.particleCanvas) {
        return;
      }
      const centerX = this.particleCanvas.clientWidth / 2;
      const centerY = this.particleCanvas.clientHeight / 2;
      for (let index = 0; index < count; index += 1) {
        const angle = Math.random() * Math.PI * 2;
        const distance = Math.random() * 140 + 40;
        this.particles.push({
          x: centerX + Math.cos(angle) * distance,
          y: centerY + Math.sin(angle) * distance,
          vx: (Math.random() - 0.5) * 1.8,
          vy: (Math.random() - 0.5) * 1.8,
          size: Math.random() * 2.4 + 1.2,
          life: 1,
          decay: Math.random() * 0.012 + 0.005,
          color,
        });
      }
    }

    animateParticles() {
      if (!this.particleCtx || !this.particleCanvas) {
        return;
      }
      const ctx = this.particleCtx;
      ctx.clearRect(0, 0, this.particleCanvas.clientWidth, this.particleCanvas.clientHeight);
      this.particles = this.particles.filter((particle) => particle.life > 0);
      this.particles.forEach((particle) => {
        particle.x += particle.vx;
        particle.y += particle.vy;
        particle.life -= particle.decay;
        ctx.save();
        ctx.globalAlpha = particle.life;
        ctx.fillStyle = particle.color;
        ctx.shadowBlur = 12;
        ctx.shadowColor = particle.color;
        ctx.beginPath();
        ctx.arc(particle.x, particle.y, particle.size, 0, Math.PI * 2);
        ctx.fill();
        ctx.restore();
      });

      if (this.currentState === "listening" && Math.random() < 0.08) {
        this.createParticles(2, "#4be3aa");
      }
      if (this.currentState === "thinking" && Math.random() < 0.08) {
        this.createParticles(2, "#ad7cff");
      }
      if (this.currentState === "speaking" && Math.random() < 0.08) {
        this.createParticles(2, "#ffae75");
      }
      window.requestAnimationFrame(() => this.animateParticles());
    }

    startBlinkLoop() {
      const schedule = () => {
        this.blinkTimer = window.setTimeout(() => {
          if (this.currentState !== "error") {
            this.blink();
          }
          schedule();
        }, Math.random() * 3200 + 1800);
      };
      schedule();
    }

    blink() {
      this.eyeNodes.concat(this.characterEyeNodes).forEach((node) => {
        node.classList.add("is-blinking");
      });
      window.setTimeout(() => {
        this.eyeNodes.concat(this.characterEyeNodes).forEach((node) => {
          node.classList.remove("is-blinking");
        });
      }, 160);
    }

    handlePointerMove(event) {
      const x = (event.clientX - window.innerWidth / 2) / 50;
      const y = (event.clientY - window.innerHeight / 2) / 50;
      const eyeX = `${Math.max(-12, Math.min(12, x))}px`;
      const eyeY = `${Math.max(-9, Math.min(9, y))}px`;
      const charX = `${Math.max(-4, Math.min(4, x * 0.4))}px`;
      const charY = `${Math.max(-3, Math.min(3, y * 0.35))}px`;
      this.stage.style.setProperty("--gaze-x", eyeX);
      this.stage.style.setProperty("--gaze-y", eyeY);
      this.stage.style.setProperty("--char-gaze-x", charX);
      this.stage.style.setProperty("--char-gaze-y", charY);
    }

    handleDocumentClick(event) {
      if (!this.stage.contains(event.target)) {
        this.setChooserOpen(false);
      }
    }

    setChooserOpen(isOpen) {
      if (!this.chooserButton || !this.chooserMenu) {
        return;
      }
      this.chooserButton.setAttribute("aria-expanded", isOpen ? "true" : "false");
      this.chooserMenu.hidden = !isOpen;
    }

    setCharacter(character) {
      if (!CHARACTER_LABELS[character]) {
        character = "eyes";
      }
      this.currentCharacter = character;
      this.stage.dataset.character = character;
      if (this.nameNode) {
        this.nameNode.textContent = CHARACTER_LABELS[character];
      }
      if (this.chooserButton) {
        this.chooserButton.textContent = `Choose Character: ${CHARACTER_LABELS[character]}`;
      }
      this.optionButtons.forEach((button) => {
        const selected = button.dataset.character === character;
        button.setAttribute("aria-pressed", selected ? "true" : "false");
        const subtitle = button.querySelector(".voice-avatar-option-subtitle");
        if (subtitle) {
          subtitle.textContent = selected ? "Selected" : CHARACTER_SUBTITLES[button.dataset.character] || "";
        }
      });
      window.localStorage.setItem(STORAGE_KEY, character);
      if (character === "komi") {
        void this.komi.init().then(() => this.komi.setState(this.currentState, { force: true }));
      }
    }

    setState(state) {
      const normalized = STAGE_STATE_MAP[state] || "idle";
      this.currentState = normalized;
      if (this.shell) {
        this.shell.dataset.state = normalized;
      }
      if (this.currentCharacter === "komi") {
        void this.komi.setState(normalized);
      }
    }
  }

  window.createVoiceAvatarStage = function createVoiceAvatarStage(options) {
    if (!options || !options.stage) {
      return null;
    }
    return new VoiceAvatarStage(options);
  };
})();
