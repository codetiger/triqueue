// SPDX-FileCopyrightText: 2026 Harishankar Narayanan
// SPDX-License-Identifier: Apache-2.0
/**
 * Hardware-Accelerated WebGL 2.0 Instanced 3D Silicon Die & Layer Stackup Renderer
 * Renders real post-route OpenROAD `6_final.def` geometry (41k+ standard cells,
 * M1-M7 / li1-met5 routed metal traces, vertical via pillars, VDD/VSS power grid,
 * and 428 perimeter I/O pins) with cycle-synchronized activity pulsing.
 */
(function () {
  "use strict";

  // Google Material Design 3 (M3 Dark Tonal Pastel Palette)
  const MODULE_META = [
    { id: 0, key: "mac_bank0", short: "Bank X",   name: "Accumulator Bank X (u_array.v0)", rgb: [0.51, 0.79, 0.58], hex: "#81c995" },
    { id: 1, key: "mac_bank1", short: "Bank Y",   name: "Accumulator Bank Y (u_array.v1)", rgb: [0.95, 0.55, 0.51], hex: "#f28b82" },
    { id: 2, key: "mac_prod",  short: "9×9 MUL",  name: "9×9 Multipliers & Prod Reg (u_array)", rgb: [0.77, 0.54, 0.98], hex: "#c58af9" },
    { id: 3, key: "qa_fifo",   short: "QA FIFO",  name: "QA Operand FIFO (u_qa)", rgb: [0.54, 0.71, 0.97], hex: "#8ab4f8" },
    { id: 4, key: "qb_fifo",   short: "QB FIFO",  name: "QB Operand FIFO (u_qb)", rgb: [0.99, 0.84, 0.39], hex: "#fdd663" },
    { id: 5, key: "mac_cmd_q", short: "MAC CmdQ", name: "MAC Command Queue (u_macq)", rgb: [0.84, 0.68, 0.98], hex: "#d7aefb" },
    { id: 6, key: "stream_eng",short: "Stream",   name: "Stream Engine & Tags (u_stream)", rgb: [0.47, 0.85, 0.93], hex: "#78d9ec" },
    { id: 7, key: "frontend",  short: "FrontEnd", name: "Front-End Decoder & Loops (u_fe)", rgb: [0.99, 0.89, 0.58], hex: "#fde293" },
    { id: 8, key: "drain_unit",short: "Drain",    name: "Drain Sequencer & Staging (u_drain)", rgb: [0.96, 0.68, 0.66], hex: "#f6aea9" },
    { id: 9, key: "regs_prog", short: "HostRegs", name: "Host Regs & 64B Program Buf (u_regs)", rgb: [0.66, 0.85, 0.71], hex: "#a8dab5" },
    { id: 10, key: "core_ctrl",short: "Ctrl/B1",  name: "Beat-1 Latches & MAC Ctrl (u_core)", rgb: [0.75, 0.62, 0.96], hex: "#bfa0f5" },
    { id: 11, key: "clock_tree",short: "CTS/ICG", name: "CTS Clock Tree & Substrate Fill", rgb: [0.52, 0.82, 0.88], hex: "#85d1e0" },
  ];

  const CLASS_META = [
    { id: 0, short: "FF",    name: "Sequential Flip-Flops (8,904 FFs)", rgb: [0.54, 0.71, 0.97], hex: "#8ab4f8" },
    { id: 1, short: "ICG",   name: "Integrated Clock Gates (102 ICGs)", rgb: [0.66, 0.85, 0.71], hex: "#a8dab5" },
    { id: 2, short: "CTS",   name: "CTS Clock Tree Buffers", rgb: [0.99, 0.84, 0.39], hex: "#fdd663" },
    { id: 3, short: "Logic", name: "Combinational Logic (9×9 MUL / CPA)", rgb: [0.68, 0.56, 0.88], hex: "#ad8fe0" },
    { id: 4, short: "Fill",  name: "Physical Decap / Well Tap / Tie", rgb: [0.22, 0.25, 0.31], hex: "#38404f" },
  ];

  const LAYER_MAT_COLORS = [
    { name: "M1 / li1",  dir: "Local",       rgb: [0.54, 0.71, 0.97], hex: "#8ab4f8", mat: "TiN/W Local" },
    { name: "M2 / met1", dir: "Horiz (E-W)", rgb: [0.47, 0.85, 0.93], hex: "#78d9ec", mat: "Horiz Cu (X)" },
    { name: "M3 / met2", dir: "Vert (N-S)",  rgb: [0.99, 0.84, 0.39], hex: "#fdd663", mat: "Vert Cu (Y)" },
    { name: "M4 / met3", dir: "Horiz (E-W)", rgb: [0.51, 0.79, 0.58], hex: "#81c995", mat: "Horiz Bus (X)" },
    { name: "M5 / met4", dir: "Vert (N-S)",  rgb: [0.77, 0.54, 0.98], hex: "#c58af9", mat: "Vert Clock/PDN (Y)" },
    { name: "M6 / met5", dir: "Horiz (E-W)", rgb: [0.99, 0.78, 0.48], hex: "#fcc67a", mat: "Horiz Global/PDN (X)" },
    { name: "M7",        dir: "Vert (N-S)",  rgb: [0.95, 0.55, 0.51], hex: "#f28b82", mat: "Top Cu (Y)" },
  ];

  // Physical layer stack per platform, in µm above the bottom of the device layer:
  // layers[i] = [bottom, thickness, LEF min width] for the data's layer index i.
  // The DEF and LEFs give x/y and widths but no heights, so heights come from:
  //   sky130hd: Magic sky130A.tech cifoutput `render` lines (li1 0.9361/0.10,
  //             met1 1.3761/0.36 ... met5 5.3711/1.26), rebased to diff (0.2062).
  //   asap7:    the tech LEF has no thickness. Conductor and inter-metal dielectric
  //             thicknesses from the ASAP7 StarRC ITF (asap7_snps asap07.itf.x1),
  //             stacked from the bottom of ACTIVE; an approximation.
  // cellTop: the device layer plus the in-cell metal (ASAP7 M1, sky130 li1); neither
  // carries routed segments in these DEFs, so cells are drawn as one slab up to it.
  const LAYER_STACK = {
    asap7: {
      cellTop: 0.3056,
      layers: [
        [0.2696, 0.036, 0.018], // M1
        [0.3452, 0.036, 0.018], // M2
        [0.4208, 0.036, 0.018], // M3
        [0.4964, 0.048, 0.024], // M4
        [0.5972, 0.048, 0.024], // M5
        [0.6980, 0.064, 0.032], // M6
        [0.8324, 0.064, 0.032], // M7
      ],
    },
    sky130hd: {
      cellTop: 0.8299,
      layers: [
        [0.7299, 0.100, 0.17], // li1
        [1.1699, 0.360, 0.14], // met1
        [1.7999, 0.360, 0.14], // met2
        [2.5799, 0.845, 0.30], // met3
        [3.8149, 0.845, 0.30], // met4
        [5.1649, 1.260, 1.60], // met5
      ],
    },
  };

  // Minimal 4x4 matrix math helpers
  function mat4Identity() {
    return new Float32Array([1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]);
  }

  function mat4Multiply(a, b) {
    const out = new Float32Array(16);
    for (let i = 0; i < 4; i++) {
      const ai0 = a[i], ai1 = a[i + 4], ai2 = a[i + 8], ai3 = a[i + 12];
      out[i]      = ai0 * b[0]  + ai1 * b[1]  + ai2 * b[2]  + ai3 * b[3];
      out[i + 4]  = ai0 * b[4]  + ai1 * b[5]  + ai2 * b[6]  + ai3 * b[7];
      out[i + 8]  = ai0 * b[8]  + ai1 * b[9]  + ai2 * b[10] + ai3 * b[11];
      out[i + 12] = ai0 * b[12] + ai1 * b[13] + ai2 * b[14] + ai3 * b[15];
    }
    return out;
  }

  function mat4Perspective(fovRad, aspect, near, far) {
    const f = 1.0 / Math.tan(fovRad / 2);
    const nf = 1 / (near - far);
    return new Float32Array([
      f / aspect, 0, 0, 0,
      0, f, 0, 0,
      0, 0, (far + near) * nf, -1,
      0, 0, 2 * far * near * nf, 0,
    ]);
  }

  function mat4LookAt(eye, center, up) {
    let zx = eye[0] - center[0], zy = eye[1] - center[1], zz = eye[2] - center[2];
    let len = Math.hypot(zx, zy, zz) || 1;
    zx /= len; zy /= len; zz /= len;
    let xx = up[1] * zz - up[2] * zy;
    let xy = up[2] * zx - up[0] * zz;
    let xz = up[0] * zy - up[1] * zx;
    len = Math.hypot(xx, xy, xz) || 1;
    xx /= len; xy /= len; xz /= len;
    const yx = zy * xz - zz * xy;
    const yy = zz * xx - zx * xz;
    const yz = zx * xy - zy * xx;
    return new Float32Array([
      xx, yx, zx, 0,
      xy, yy, zy, 0,
      xz, yz, zz, 0,
      -(xx * eye[0] + xy * eye[1] + xz * eye[2]),
      -(yx * eye[0] + yy * eye[1] + yz * eye[2]),
      -(zx * eye[0] + zy * eye[1] + zz * eye[2]),
      1,
    ]);
  }

  function heatColor(t) {
    const v = Math.max(0, Math.min(1, t));
    if (v < 0.33) {
      const u = v / 0.33;
      return [0.06 + 0.0 * u, 0.25 + 0.65 * u, 0.55 + 0.45 * u];
    } else if (v < 0.66) {
      const u = (v - 0.33) / 0.33;
      return [0.06 + 0.92 * u, 0.90 - 0.15 * u, 1.0 - 0.85 * u];
    } else {
      const u = (v - 0.66) / 0.34;
      return [0.98, 0.75 - 0.55 * u, 0.15 + 0.25 * u];
    }
  }

  class Chip3DRenderer {
    constructor(canvas, onHoverCallback) {
      this.canvas = canvas;
      this.onHover = onHoverCallback;
      this.gl = canvas.getContext("webgl2", { antialias: true, alpha: false, powerPreference: "high-performance" });

      this.chipData = null;
      this.colorMode = "module"; // "material" | "module" | "cell_class" | "activity" | "clock_xray"
      this.layerGap = 1.0;       // world units inserted between metal layers (0 = real stack)
      this.k = 1;                // world units per µm (set from the die size)
      this.highlightMod = -1;    // -1 = all, 0..11 = isolated module
      this.pulsePhase = 0;

      // Live cycle activity: which modules are busy in the current cycle. The target
      // is set per cycle; the level eases toward it every frame (fast rise, slow fade)
      // and goes to the shader as a uniform, so stepping cycles never rebuilds geometry.
      this.live = true;
      this.actTarget = new Float32Array(MODULE_META.length);
      this.actLevel = new Float32Array(MODULE_META.length);
      this._lastNow = 0;

      // Visibility flags
      this.vis = {
        substrate: true,
        regions: false,
        cells: true,
        filler: true,
        vias: true,
        pdn: true,
        pins: true,
        layers: [true, true, true, true, true, true, true],
      };

      // Spherical Orbit Camera State (normalized die coordinates: -50 .. +50, +X = East, +Y = North, +Z = Up)
      this.cam = {
        theta: -0.62,
        phi: 0.82,
        radius: 150,
        target: [0, 0, 1.5],
        goalTheta: -0.62,
        goalPhi: 0.82,
        goalRadius: 150,
        goalTarget: [0, 0, 1.5],
      };

      this.eye = [0, -100, 80];
      this.instancesByLayer = {};
      this.totalBoxes = 0;

      if (this.gl) {
        this._initWebGL2();
      }
      this._bindEvents();
      this._startLoop();
    }

    _initWebGL2() {
      const gl = this.gl;
      const vsSource = `#version 300 es
        precision highp float;
        layout(location = 0) in vec3 aPos;
        layout(location = 1) in vec3 aNormal;
        // Per-instance attributes
        layout(location = 2) in vec3 iCenter;   // x, y (world), z0 (µm; world for decoration)
        layout(location = 3) in vec3 iScale;    // w, d (world), h (µm; world for decoration)
        layout(location = 4) in vec4 iColor;    // r, g, b, alpha
        layout(location = 5) in vec2 iMeta;     // slot (-1 decoration, 0 cells + in-cell metal, n = metal n), modId (-1 none, -2 via)

        uniform mat4 uViewProj;
        uniform float uZScale;    // world units per µm: the same scale as x/y, so heights are true
        uniform float uGap;       // extra world-unit gap inserted below each metal slot (0 = real stack)
        uniform float uHighlightMod;
        uniform float uPulseTime;
        uniform float uLive;      // 1 = colour the die by this cycle's block activity
        uniform float uAct[12];   // per-module activity level 0..1, eased in JS

        out vec3 vNormal;
        out vec3 vLocalPos;
        out vec4 vColor;
        out float vDimmed;

        void main() {
          float slot = iMeta.x;
          float modId = iMeta.y;
          bool isVia = modId < -1.5;
          bool isModule = modId > -0.5;
          float act = isModule ? uAct[min(int(modId + 0.5), 11)] : 0.0;
          bool live = uLive > 0.5 && isModule;

          // Thickness and height are true scale (µm x the x/y scale). The layer gap only
          // lifts each metal slot; a via stretches across the gap to the slot above.
          float t = aPos.z + 0.5;
          float z;
          if (slot < -0.5) {
            z = iCenter.z + t * iScale.z;
          } else {
            z = (iCenter.z + t * iScale.z) * uZScale + (slot + (isVia ? t : 0.0)) * uGap;
          }
          vec3 worldPos = vec3(iCenter.x + aPos.x * iScale.x, iCenter.y + aPos.y * iScale.y, z);

          gl_Position = uViewProj * vec4(worldPos, 1.0);
          vNormal = aNormal;
          vLocalPos = aPos;

          vec4 col = iColor;
          if (live) {
            // Idle: dim and greyed. Busy: full module colour, brightened, with a soft throb.
            float grey = dot(col.rgb, vec3(0.30, 0.59, 0.11));
            vec3 idle = mix(vec3(grey), col.rgb, 0.45) * 0.30;
            float throb = 0.85 + 0.15 * sin(uPulseTime * 7.0);
            vec3 busy = min(col.rgb * (1.05 + 0.30 * throb), vec3(1.0));
            col.rgb = mix(idle, busy, act);
          } else if (uLive > 0.5 && (slot > 0.5 || isVia)) {
            // Routing, vias, power and pins fade back so the busy cells read through them
            col.rgb *= 0.55;
            col.a *= 0.30;
          }

          float dimmed = 0.0;
          if (uHighlightMod >= -0.5 && abs(modId - uHighlightMod) > 0.2 && modId >= -0.5) {
            col.rgb *= 0.22;
            col.a *= 0.28;
            dimmed = 1.0;
          }
          vColor = col;
          vDimmed = dimmed;
        }
      `;

      const fsSource = `#version 300 es
        precision highp float;
        in vec3 vNormal;
        in vec3 vLocalPos;
        in vec4 vColor;
        in float vDimmed;
        out vec4 fragColor;

        void main() {
          vec3 N = normalize(vNormal);
          vec3 L1 = normalize(vec3(0.45, -0.55, 0.70));
          vec3 L2 = normalize(vec3(-0.60, 0.40, 0.50));
          float diff1 = max(dot(N, L1), 0.0);
          float diff2 = max(dot(N, L2), 0.0) * 0.35;
          float ambient = 0.42;

          // Subtle edge bevel highlight on box borders
          vec3 absP = abs(vLocalPos) * 2.0;
          float edge = max(max(absP.x * absP.y, absP.y * absP.z), absP.x * absP.z);
          float rim = smoothstep(0.86, 0.99, edge) * (vDimmed > 0.5 ? 0.04 : 0.16);

          // Top face metallic specular sheen
          float topGlow = N.z > 0.8 ? 0.14 : 0.0;
          vec3 lit = vColor.rgb * (ambient + diff1 * 0.58 + diff2) + vec3(rim + topGlow);
          fragColor = vec4(lit, vColor.a);
        }
      `;

      const vs = this._compileShader(gl.VERTEX_SHADER, vsSource);
      const fs = this._compileShader(gl.FRAGMENT_SHADER, fsSource);
      const prog = gl.createProgram();
      gl.attachShader(prog, vs);
      gl.attachShader(prog, fs);
      gl.linkProgram(prog);
      this.program = prog;

      this.uViewProj = gl.getUniformLocation(prog, "uViewProj");
      this.uZScale = gl.getUniformLocation(prog, "uZScale");
      this.uGap = gl.getUniformLocation(prog, "uGap");
      this.uHighlightMod = gl.getUniformLocation(prog, "uHighlightMod");
      this.uPulseTime = gl.getUniformLocation(prog, "uPulseTime");
      this.uLive = gl.getUniformLocation(prog, "uLive");
      this.uAct = gl.getUniformLocation(prog, "uAct");

      // Unit cube geometry [-0.5 .. +0.5]
      const verts = new Float32Array([
        // Top (+Z)
        -0.5,-0.5, 0.5,  0,0,1,   0.5,-0.5, 0.5,  0,0,1,   0.5, 0.5, 0.5,  0,0,1,
        -0.5,-0.5, 0.5,  0,0,1,   0.5, 0.5, 0.5,  0,0,1,  -0.5, 0.5, 0.5,  0,0,1,
        // Bottom (-Z)
        -0.5,-0.5,-0.5,  0,0,-1, -0.5, 0.5,-0.5,  0,0,-1,  0.5, 0.5,-0.5,  0,0,-1,
        -0.5,-0.5,-0.5,  0,0,-1,  0.5, 0.5,-0.5,  0,0,-1,  0.5,-0.5,-0.5,  0,0,-1,
        // Front (-Y)
        -0.5,-0.5,-0.5,  0,-1,0,  0.5,-0.5,-0.5,  0,-1,0,  0.5,-0.5, 0.5,  0,-1,0,
        -0.5,-0.5,-0.5,  0,-1,0,  0.5,-0.5, 0.5,  0,-1,0, -0.5,-0.5, 0.5,  0,-1,0,
        // Back (+Y)
        -0.5, 0.5,-0.5,  0,1,0,  -0.5, 0.5, 0.5,  0,1,0,   0.5, 0.5, 0.5,  0,1,0,
        -0.5, 0.5,-0.5,  0,1,0,   0.5, 0.5, 0.5,  0,1,0,   0.5, 0.5,-0.5,  0,1,0,
        // Right (+X)
         0.5,-0.5,-0.5,  1,0,0,   0.5, 0.5,-0.5,  1,0,0,   0.5, 0.5, 0.5,  1,0,0,
         0.5,-0.5,-0.5,  1,0,0,   0.5, 0.5, 0.5,  1,0,0,   0.5,-0.5, 0.5,  1,0,0,
        // Left (-X)
        -0.5,-0.5,-0.5, -1,0,0,  -0.5,-0.5, 0.5, -1,0,0,  -0.5, 0.5, 0.5, -1,0,0,
        -0.5,-0.5,-0.5, -1,0,0,  -0.5, 0.5, 0.5, -1,0,0,  -0.5, 0.5,-0.5, -1,0,0,
      ]);

      this.vao = gl.createVertexArray();
      gl.bindVertexArray(this.vao);

      const vbo = gl.createBuffer();
      gl.bindBuffer(gl.ARRAY_BUFFER, vbo);
      gl.bufferData(gl.ARRAY_BUFFER, verts, gl.STATIC_DRAW);

      gl.enableVertexAttribArray(0);
      gl.vertexAttribPointer(0, 3, gl.FLOAT, false, 24, 0);
      gl.enableVertexAttribArray(1);
      gl.vertexAttribPointer(1, 3, gl.FLOAT, false, 24, 12);

      this.instanceBuffer = gl.createBuffer();
      gl.bindBuffer(gl.ARRAY_BUFFER, this.instanceBuffer);
      const stride = 12 * 4; // 12 floats per instance
      // iCenter (loc 2)
      gl.enableVertexAttribArray(2);
      gl.vertexAttribPointer(2, 3, gl.FLOAT, false, stride, 0);
      gl.vertexAttribDivisor(2, 1);
      // iScale (loc 3)
      gl.enableVertexAttribArray(3);
      gl.vertexAttribPointer(3, 3, gl.FLOAT, false, stride, 12);
      gl.vertexAttribDivisor(3, 1);
      // iColor (loc 4)
      gl.enableVertexAttribArray(4);
      gl.vertexAttribPointer(4, 4, gl.FLOAT, false, stride, 24);
      gl.vertexAttribDivisor(4, 1);
      // iMeta (loc 5)
      gl.enableVertexAttribArray(5);
      gl.vertexAttribPointer(5, 2, gl.FLOAT, false, stride, 40);
      gl.vertexAttribDivisor(5, 1);

      gl.bindVertexArray(null);
    }

    _compileShader(type, src) {
      const gl = this.gl;
      const sh = gl.createShader(type);
      gl.shaderSource(sh, src);
      gl.compileShader(sh);
      if (!gl.getShaderParameter(sh, gl.COMPILE_STATUS)) {
        console.error("Shader error:", gl.getShaderInfoLog(sh));
      }
      return sh;
    }

    setChipData(data) {
      this.chipData = data;
      this.rebuildInstances();
    }

    setColorMode(mode) {
      this.colorMode = mode;
      this.rebuildInstances();
    }

    /** Space inserted between metal layers, in world units (0 = the real, touching stack). */
    setLayerGap(val) {
      this.layerGap = Math.max(0, parseFloat(val) || 0);
    }

    setHighlightModule(modId) {
      this.highlightMod = modId;
    }

    /** Mark the modules (MODULE_META keys) that are busy in the current cycle. */
    setActiveModules(keys) {
      const busy = new Set(keys);
      MODULE_META.forEach((m) => {
        this.actTarget[m.id] = busy.has(m.key) ? 1 : 0;
      });
    }

    setLive(on) {
      this.live = !!on;
    }

    _easeActivity(dt) {
      const rise = 1 - Math.exp(-dt / 0.04);
      const fade = 1 - Math.exp(-dt / 0.30);
      for (let i = 0; i < this.actLevel.length; i++) {
        const t = this.actTarget[i];
        const l = this.actLevel[i];
        this.actLevel[i] = l + (t - l) * (t > l ? rise : fade);
      }
    }

    flyToPreset(preset) {
      if (preset === "iso") {
        this.cam.goalTheta = -0.62;
        this.cam.goalPhi = 0.82;
        this.cam.goalRadius = 150;
        this.cam.goalTarget = [0, 0, 1.5];
        this.highlightMod = -1;
      } else if (preset === "top") {
        // Plan view: +Y (north) up, +X (east) right, as in the DEF
        this.cam.goalTheta = 0.0;
        this.cam.goalPhi = 0.01;
        this.cam.goalRadius = 150;
        this.cam.goalTarget = [0, 0, 0];
      } else if (preset === "side") {
        // Low view from the south: shows the layer stack in profile
        this.cam.goalTheta = 0.0;
        this.cam.goalPhi = 1.40;
        this.cam.goalRadius = 125;
        this.cam.goalTarget = [0, 0, 3];
      }
    }

    flyToModule(modKey) {
      if (!this.chipData || !this.chipData.bookmarks || !this.chipData.bookmarks[modKey]) {
        this.flyToPreset("iso");
        return;
      }
      const bm = this.chipData.bookmarks[modKey];
      const f = this._dieFrame();
      this.cam.goalTarget = [f.X(bm.cx), f.Y(bm.cy), 1.0];
      this.cam.goalRadius = 58;
      this.cam.goalPhi = 0.62;
      this.highlightMod = bm.id;
    }

    /**
     * Die µm -> world mapping. One scale for x and y (100 world units across the longer
     * die side), origin at the die centre, +X east, +Y north: the DEF's own axes.
     */
    _dieFrame() {
      const die = this.chipData.die;
      const k = 100.0 / Math.max(die.width_um, die.height_um);
      const ox = die.width_um * 0.5;
      const oy = die.height_um * 0.5;
      return { k, ox, oy, X: (um) => (um - ox) * k, Y: (um) => (um - oy) * k };
    }

    _stack() {
      return LAYER_STACK[this.chipData.platform] || LAYER_STACK.asap7;
    }

    rebuildInstances() {
      if (!this.chipData || !this.gl) return;
      const d = this.chipData;
      const { k, X, Y } = this._dieFrame();
      this.k = k;
      const stack = this._stack();
      const dieWk = d.die.width_um * k;
      const dieHk = d.die.height_um * k;
      const isDbu = d.cell_units === "dbu" && d.dbu > 0;
      const toUm = (v) => (isDbu ? v / d.dbu : v);
      // Smallest drawn wire/via/pin width in world units: real ASAP7 / sky130 wires are
      // 0.01-0.03 world units wide, far below a pixel, so they are drawn at least this wide.
      const minW = 0.10;

      // Instance layout (12 floats): x, y, z0, sx, sy, sz, r, g, b, a, slot, modId
      //   slot -1: decoration, z in world units
      //   slot  0: cells and the in-cell metal (ASAP7 M1 / sky130 li1), z in µm
      //   slot  n: metal layer n (data layer index), z in µm, lifted n x the layer gap
      const list = [];
      function pushBox(cx, cy, z0, sx, sy, sz, r, g, b, a, slot, modId) {
        list.push(cx, cy, z0, sx, sy, sz, r, g, b, a, slot, modId);
      }
      const layerOf = (lidx) => stack.layers[Math.min(lidx, stack.layers.length - 1)];

      // 1. Substrate, seal ring and core outline (decoration, just below the cells)
      if (this.vis.substrate) {
        pushBox(0, 0, -1.5, dieWk + 4, dieHk + 4, 1.4, 0.04, 0.07, 0.12, 1.0, -1, -1);
        const ex = dieWk * 0.5 + 0.6;
        const ey = dieHk * 0.5 + 0.6;
        pushBox(0, -ey, -0.08, dieWk + 1.6, 0.45, 0.06, 0.18, 0.34, 0.52, 1.0, -1, -1);
        pushBox(0, ey, -0.08, dieWk + 1.6, 0.45, 0.06, 0.22, 0.52, 0.72, 1.0, -1, -1); // north edge brighter
        pushBox(-ex, 0, -0.08, 0.45, dieHk + 1.6, 0.06, 0.18, 0.34, 0.52, 1.0, -1, -1);
        pushBox(ex, 0, -0.08, 0.45, dieHk + 1.6, 0.06, 0.18, 0.34, 0.52, 1.0, -1, -1);
        if (typeof d.die.core_x0_um === "number") {
          const cx0 = X(d.die.core_x0_um), cx1 = X(d.die.core_x1_um);
          const cy0 = Y(d.die.core_y0_um), cy1 = Y(d.die.core_y1_um);
          const cw = cx1 - cx0, ch = cy1 - cy0;
          const cmx = (cx0 + cx1) * 0.5, cmy = (cy0 + cy1) * 0.5;
          pushBox(cmx, cy0, -0.08, cw, 0.18, 0.06, 0.25, 0.48, 0.68, 0.75, -1, -1);
          pushBox(cmx, cy1, -0.08, cw, 0.18, 0.06, 0.25, 0.48, 0.68, 0.75, -1, -1);
          pushBox(cx0, cmy, -0.08, 0.18, ch, 0.06, 0.25, 0.48, 0.68, 0.75, -1, -1);
          pushBox(cx1, cmy, -0.08, 0.18, ch, 0.06, 0.25, 0.48, 0.68, 0.75, -1, -1);
        }
      }

      // 1B. Module floorplan regions: a translucent sheet on top of the cell layer
      if (this.vis.regions && d.bookmarks) {
        for (const key of Object.keys(d.bookmarks)) {
          const bm = d.bookmarks[key];
          const m = MODULE_META[bm.id];
          if (!m) continue;
          const inset = key === "mac_bank1" ? 0.42 : 0.0;
          const x0 = X(bm.x0) + inset, x1 = X(bm.x1) - inset;
          const y0 = Y(bm.y0) + inset, y1 = Y(bm.y1) - inset;
          const cx = (x0 + x1) * 0.5, cy = (y0 + y1) * 0.5;
          const bw = Math.max(3.0, x1 - x0), bh = Math.max(3.0, y1 - y0);
          const [r, g, b] = m.rgb;
          const z = stack.cellTop;
          if (key !== "mac_bank1") {
            pushBox(cx, cy, z, bw, bh, 0.04 * z, r * 0.55, g * 0.55, b * 0.55, 0.22, 0, bm.id);
          }
          const rail = 0.20;
          pushBox(cx, y0, z, bw, rail, 0.12 * z, r, g, b, 0.75, 0, bm.id);
          pushBox(cx, y1, z, bw, rail, 0.12 * z, r, g, b, 0.75, 0, bm.id);
          pushBox(x0, cy, z, rail, bh, 0.12 * z, r, g, b, 0.75, 0, bm.id);
          pushBox(x1, cy, z, rail, bh, 0.12 * z, r, g, b, 0.75, 0, bm.id);
        }
      }

      // 2. Standard cells and fill at their DEF position and LEF footprint. Every cell
      //    occupies the same device layer, so every cell has the same height; class and
      //    activity are shown by colour only.
      if (this.vis.cells) {
        const cellH = stack.cellTop;
        for (let i = 0; i < d.cells.length; i++) {
          const [x, y, w, h, mod, cclass, , rawAct] = d.cells[i];
          if (cclass === 4 && !this.vis.filler) continue;

          const act = isDbu ? rawAct * 0.01 : rawAct;
          const wu = toUm(w), hu = toUm(h);
          const cx = X(toUm(x) + wu * 0.5);
          const cy = Y(toUm(y) + hu * 0.5);
          // A sliver of gap between neighbours so cell edges stay visible
          const sx = Math.max(0.03, wu * k * 0.992);
          const sy = Math.max(0.05, hu * k * 0.955);

          let r = 0.3, g = 0.5, b = 0.8, a = 0.97;
          if (this.colorMode === "module") {
            if (cclass === 4) {
              r = 0.14; g = 0.18; b = 0.25; a = 0.94;
            } else {
              const m = MODULE_META[mod] || MODULE_META[11];
              r = m.rgb[0]; g = m.rgb[1]; b = m.rgb[2];
              if (cclass === 3) { r *= 0.72; g *= 0.72; b *= 0.72; }
              if (cclass === 1) { r = Math.min(1, r + 0.28); g = Math.min(1, g + 0.28); b = Math.min(1, b + 0.28); }
            }
          } else if (this.colorMode === "cell_class") {
            const cm = CLASS_META[cclass] || CLASS_META[3];
            r = cm.rgb[0]; g = cm.rgb[1]; b = cm.rgb[2];
          } else if (this.colorMode === "material") {
            if (cclass === 1) { r = 0.78; g = 0.92; b = 0.28; }
            else if (cclass === 0) { r = 0.16; g = 0.58; b = 0.72; }
            else if (cclass === 4) { r = 0.12; g = 0.17; b = 0.25; }
            else { r = 0.22; g = 0.38; b = 0.54; }
          } else if (this.colorMode === "activity") {
            if (cclass === 4) {
              r = 0.08; g = 0.12; b = 0.19; a = 0.88;
            } else {
              // Average switching activity over the gemm64 power run (static per cell)
              const hc = heatColor(act * 0.75);
              r = hc[0]; g = hc[1]; b = hc[2];
            }
          } else if (this.colorMode === "clock_xray") {
            if (cclass === 1) { r = 0.55; g = 1.0; b = 0.18; }
            else if (cclass === 2) { r = 0.0; g = 0.92; b = 1.0; }
            else if (mod === 5 || mod === 2) { r = 1.0; g = 0.32; b = 0.52; }
            else { r = 0.10; g = 0.14; b = 0.22; a = 0.32; }
          }

          pushBox(cx, cy, 0.0, sx, sy, cellH, r, g, b, a, 0, cclass === 4 ? -1 : mod);
        }
      }

      // 3. Vias: from the top of their (lower) layer to the bottom of the layer above;
      //    the shader also stretches them across the layer gap
      if (this.vis.vias && d.vias) {
        for (let i = 0; i < d.vias.length; i++) {
          const [vx, vy, lidx, ntype] = d.vias[i];
          if (!this.vis.layers[lidx] || lidx + 1 >= stack.layers.length) continue;
          if (this.colorMode === "clock_xray" && ntype !== 1 && i % 4 !== 0) continue;
          const lo = stack.layers[lidx];
          const hi = stack.layers[lidx + 1];
          const z0 = lo[0] + lo[1];
          const vw = Math.max(minW, lo[2] * k);
          const r = ntype === 1 ? 0.45 : 0.78;
          const g = ntype === 1 ? 0.98 : 0.84;
          const b = ntype === 1 ? 1.00 : 0.92;
          pushBox(X(vx), Y(vy), z0, vw, vw, Math.max(0.001, hi[0] - z0), r, g, b, 0.92, lidx, -2);
        }
      }

      // 4. Routed wire segments, on their layer's real height and thickness
      if (d.wires) {
        for (let i = 0; i < d.wires.length; i++) {
          const [x1, y1, x2, y2, lidx, ntype] = d.wires[i];
          if (!this.vis.layers[lidx]) continue;
          if (this.colorMode === "clock_xray" && ntype !== 1 && ntype !== 3 && i % 6 !== 0) continue;

          const L = layerOf(lidx);
          const ww = Math.max(minW, L[2] * k);
          const sx = Math.max(ww, Math.abs(x2 - x1) * k);
          const sy = Math.max(ww, Math.abs(y2 - y1) * k);

          const lm = LAYER_MAT_COLORS[Math.min(lidx, LAYER_MAT_COLORS.length - 1)];
          let r = lm.rgb[0], g = lm.rgb[1], b = lm.rgb[2];
          let modTag = -1;
          if (ntype === 2) modTag = 3;
          else if (ntype === 3) modTag = 2;

          if (this.colorMode === "clock_xray") {
            if (ntype === 1) { r = 0.15; g = 0.98; b = 1.0; }
            else if (ntype === 3) { r = 1.0; g = 0.38; b = 0.18; }
            else { r *= 0.25; g *= 0.25; b *= 0.25; }
          } else if (this.colorMode === "module" && ntype > 0) {
            if (ntype === 1) { r = 0.13; g = 0.90; b = 0.96; }
            else if (ntype === 2) { r = 0.0; g = 0.94; b = 1.0; }
            else if (ntype === 3) { r = 0.65; g = 0.45; b = 0.98; }
          }

          pushBox(X((x1 + x2) * 0.5), Y((y1 + y2) * 0.5), L[0], sx, sy, L[1], r, g, b, 0.92, lidx, modTag);
        }
      }

      // 5. Power grid straps (SPECIALNETS VDD / VSS) at their real width and layer
      if (this.vis.pdn && d.pdn) {
        for (let i = 0; i < d.pdn.length; i++) {
          const [x1, y1, x2, y2, wUm, lidx, isVdd] = d.pdn[i];
          const layerIdx = typeof lidx === "number" ? lidx : stack.layers.length - 1;
          if (this.vis.layers[layerIdx] === false) continue;
          const L = layerOf(layerIdx);
          const sx = Math.max(wUm * k, Math.abs(x2 - x1) * k);
          const sy = Math.max(wUm * k, Math.abs(y2 - y1) * k);
          const r = isVdd ? 0.98 : 0.92;
          const g = isVdd ? 0.78 : 0.24;
          const b = isVdd ? 0.18 : 0.38;
          pushBox(X((x1 + x2) * 0.5), Y((y1 + y2) * 0.5), L[0], sx, sy, L[1], r, g, b, 0.82, layerIdx, -1);
        }
      }

      // 6. I/O pins on their real layer at the die edge (enlarged to stay visible)
      if (this.vis.pins && d.pins) {
        for (let i = 0; i < d.pins.length; i++) {
          const p = d.pins[i];
          const pl = Math.min(typeof p.lidx === "number" ? p.lidx : 3, stack.layers.length - 1);
          const L = stack.layers[pl];
          const sx = Math.max(0.24, (p.w || 0.3) * k);
          const sy = Math.max(0.24, (p.h || 0.3) * k);
          let r = 0.0, g = 0.94, b = 1.0;
          if (p.group === "wr_port") { r = 0.96; g = 0.25; b = 0.37; }
          else if (p.group === "reg_port") { r = 0.64; g = 0.90; b = 0.21; }
          else if (p.group === "clk_rst") { r = 1.0; g = 0.85; b = 0.10; }
          pushBox(X(p.x), Y(p.y), L[0], sx, sy, L[1], r, g, b, 1.0, pl, -1);
        }
      }

      const arr = new Float32Array(list);
      this.totalBoxes = arr.length / 12;
      const gl = this.gl;
      gl.bindBuffer(gl.ARRAY_BUFFER, this.instanceBuffer);
      gl.bufferData(gl.ARRAY_BUFFER, arr, gl.DYNAMIC_DRAW);
    }

    _bindEvents() {
      let dragging = false;
      let dragMode = "orbit"; // "orbit" | "pan"
      let lastX = 0, lastY = 0;

      this.canvas.addEventListener("contextmenu", (e) => e.preventDefault());
      this.canvas.addEventListener("mousedown", (e) => {
        dragging = true;
        dragMode = e.button === 2 || e.shiftKey ? "pan" : "orbit";
        lastX = e.clientX;
        lastY = e.clientY;
      });

      window.addEventListener("mouseup", () => {
        dragging = false;
      });

      window.addEventListener("mousemove", (e) => {
        if (!dragging) {
          this._handleHover(e);
          return;
        }
        const dx = e.clientX - lastX;
        const dy = e.clientY - lastY;
        lastX = e.clientX;
        lastY = e.clientY;

        if (dragMode === "orbit") {
          this.cam.goalTheta -= dx * 0.0075;
          this.cam.goalPhi = Math.max(0.01, Math.min(1.52, this.cam.goalPhi - dy * 0.0075));
        } else {
          const panScale = this.cam.radius * 0.0016;
          const cosT = Math.cos(this.cam.theta);
          const sinT = Math.sin(this.cam.theta);
          this.cam.goalTarget[0] -= (dx * cosT + dy * sinT) * panScale;
          this.cam.goalTarget[1] -= (dx * sinT - dy * cosT) * panScale;
        }
      });

      this.canvas.addEventListener(
        "wheel",
        (e) => {
          e.preventDefault();
          const factor = e.deltaY > 0 ? 1.09 : 0.91;
          this.cam.goalRadius = Math.max(22, Math.min(240, this.cam.goalRadius * factor));
        },
        { passive: false }
      );
    }

    _handleHover(e) {
      if (!this.chipData || !this.onHover) return;
      const rect = this.canvas.getBoundingClientRect();
      if (
        e.clientX < rect.left ||
        e.clientX > rect.right ||
        e.clientY < rect.top ||
        e.clientY > rect.bottom
      ) {
        return;
      }

      // Intersect 3D camera ray through cursor with the Z = 0 silicon wafer plane
      const ndcX = ((e.clientX - rect.left) / rect.width) * 2.0 - 1.0;
      const ndcY = 1.0 - ((e.clientY - rect.top) / rect.height) * 2.0;
      const aspect = rect.width / Math.max(1, rect.height);
      const tanHalfFov = Math.tan((20 * Math.PI) / 180);

      const eye = this.eye || [0, -100, 80];
      const tgt = this.cam.target;
      let fx = tgt[0] - eye[0], fy = tgt[1] - eye[1], fz = tgt[2] - eye[2];
      const flen = Math.hypot(fx, fy, fz) || 1;
      fx /= flen; fy /= flen; fz /= flen;

      // Right = normalize(cross(f, [0,0,1])) = [fy, -fx, 0]
      let rx = fy, ry = -fx, rz = 0;
      const rlen = Math.hypot(rx, ry) || 1;
      rx /= rlen; ry /= rlen;

      // Up = cross(r, f)
      const ux = ry * fz - rz * fy;
      const uy = rz * fx - rx * fz;
      const uz = rx * fy - ry * fx;

      const sx = ndcX * aspect * tanHalfFov;
      const sy = ndcY * tanHalfFov;
      const dirX = fx + sx * rx + sy * ux;
      const dirY = fy + sx * ry + sy * uy;
      const dirZ = fz + sx * rz + sy * uz;

      let worldX = ndcX * 50.0;
      let worldY = ndcY * 50.0;
      if (Math.abs(dirZ) > 1e-4) {
        const t = -eye[2] / dirZ;
        if (t > 0) {
          worldX = eye[0] + t * dirX;
          worldY = eye[1] + t * dirY;
        }
      }

      const f = this._dieFrame();
      const umX = Math.max(0, Math.min(this.chipData.die.width_um, worldX / f.k + f.ox));
      const umY = Math.max(0, Math.min(this.chipData.die.height_um, worldY / f.k + f.oy));

      // Find module bookmark containing (umX, umY) or closest centroid
      let bestBm = null;
      let bestScore = 1e18;
      const bms = this.chipData.bookmarks || {};
      for (const k of Object.keys(bms)) {
        const b = bms[k];
        const inside = umX >= b.x0 && umX <= b.x1 && umY >= b.y0 && umY <= b.y1;
        const d2 = (umX - b.cx) ** 2 + (umY - b.cy) ** 2;
        const score = inside ? d2 * 0.15 : d2;
        if (score < bestScore) {
          bestScore = score;
          bestBm = b;
        }
      }
      this.onHover({
        x_um: umX.toFixed(1),
        y_um: umY.toFixed(1),
        module: bestBm,
      });
    }

    _startLoop() {
      const render = (now) => {
        const dt = this._lastNow ? Math.min(0.1, (now - this._lastNow) * 0.001) : 0;
        this._lastNow = now;
        this.pulsePhase = now * 0.001;
        this._easeActivity(dt);
        this._drawFrame();
        requestAnimationFrame(render);
      };
      requestAnimationFrame(render);
    }

    _drawCompass() {
      const c = document.getElementById("chip3d-compass");
      if (!c) return;
      const ctx = c.getContext("2d");
      const w = c.width, h = c.height;
      const cx = w * 0.5, cy = h * 0.5, rad = w * 0.36;
      ctx.clearRect(0, 0, w, h);

      ctx.fillStyle = "rgba(8, 13, 24, 0.82)";
      ctx.beginPath();
      ctx.arc(cx, cy, w * 0.46, 0, Math.PI * 2);
      ctx.fill();
      ctx.strokeStyle = "rgba(148, 163, 184, 0.25)";
      ctx.lineWidth = 1;
      ctx.stroke();

      const cosT = Math.cos(this.cam.theta);
      const sinT = Math.sin(this.cam.theta);
      const cosP = Math.cos(this.cam.phi);
      const sinP = Math.sin(this.cam.phi);

      // Project 3D unit axes (+X East, +Y North, +Z Stack) into 2D screen space
      const axes = [
        { label: "E (+X)", x: cosT,  y: sinT * cosP,  color: "#8ab4f8" },
        { label: "N (+Y)", x: sinT,  y: -cosT * cosP, color: "#81c995" },
        { label: "Z",      x: 0,     y: -sinP,        color: "#fdd663" },
      ];

      ctx.font = "700 9px 'JetBrains Mono', monospace";
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";
      for (const ax of axes) {
        const ex = cx + ax.x * rad;
        const ey = cy + ax.y * rad;
        ctx.strokeStyle = ax.color;
        ctx.lineWidth = 2;
        ctx.beginPath();
        ctx.moveTo(cx, cy);
        ctx.lineTo(ex, ey);
        ctx.stroke();
        ctx.fillStyle = ax.color;
        ctx.fillText(ax.label.split(" ")[0], cx + ax.x * (rad + 8), cy + ax.y * (rad + 8));
      }
    }

    _drawFrame() {
      const gl = this.gl;
      if (!gl) return;

      const dpr = Math.min(window.devicePixelRatio || 1, 2);
      const w = Math.floor(this.canvas.clientWidth * dpr);
      const h = Math.floor(this.canvas.clientHeight * dpr);
      if (w > 0 && h > 0 && (this.canvas.width !== w || this.canvas.height !== h)) {
        this.canvas.width = w;
        this.canvas.height = h;
      }
      if (this.canvas.width === 0 || this.canvas.height === 0) return;

      // Smoothly damp camera toward goal
      this.cam.theta += (this.cam.goalTheta - this.cam.theta) * 0.14;
      this.cam.phi += (this.cam.goalPhi - this.cam.phi) * 0.14;
      this.cam.radius += (this.cam.goalRadius - this.cam.radius) * 0.14;
      for (let i = 0; i < 3; i++) {
        this.cam.target[i] += (this.cam.goalTarget[i] - this.cam.target[i]) * 0.14;
      }

      const r = this.cam.radius;
      const sinP = Math.sin(this.cam.phi);
      const cosP = Math.cos(this.cam.phi);
      const sinT = Math.sin(this.cam.theta);
      const cosT = Math.cos(this.cam.theta);

      const eye = [
        this.cam.target[0] + r * sinP * sinT,
        this.cam.target[1] - r * sinP * cosT,
        this.cam.target[2] + r * cosP,
      ];
      this.eye = eye;

      const upVec = sinP < 0.03 ? [sinT, cosT, 0] : [0, 0, 1];
      const proj = mat4Perspective((40 * Math.PI) / 180, this.canvas.width / this.canvas.height, 1.0, 600.0);
      const view = mat4LookAt(eye, this.cam.target, upVec);
      const vp = mat4Multiply(proj, view);

      gl.viewport(0, 0, this.canvas.width, this.canvas.height);
      gl.clearColor(0.078, 0.090, 0.118, 1.0);
      gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT);
      gl.enable(gl.DEPTH_TEST);
      gl.enable(gl.BLEND);
      gl.blendFunc(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA);

      if (this.totalBoxes > 0) {
        gl.useProgram(this.program);
        gl.uniformMatrix4fv(this.uViewProj, false, vp);
        gl.uniform1f(this.uZScale, this.k);
        gl.uniform1f(this.uGap, this.layerGap);
        gl.uniform1f(this.uHighlightMod, this.highlightMod);
        gl.uniform1f(this.uPulseTime, this.pulsePhase);
        gl.uniform1f(this.uLive, this.live ? 1.0 : 0.0);
        gl.uniform1fv(this.uAct, this.actLevel);

        gl.bindVertexArray(this.vao);
        gl.drawArraysInstanced(gl.TRIANGLES, 0, 36, this.totalBoxes);
        gl.bindVertexArray(null);
      }

      this._drawCompass();
    }
  }

  window.TriQueueChip3D = {
    Chip3DRenderer,
    MODULE_META,
    CLASS_META,
    LAYER_MAT_COLORS,
  };
})();
