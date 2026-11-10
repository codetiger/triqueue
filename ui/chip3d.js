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
      this.explode = 2.8;        // Z-axis layer separation multiplier
      this.highlightMod = -1;    // -1 = all, 0..11 = isolated module
      this.activeCycleMods = new Set();
      this.pulsePhase = 0;

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
        radius: 118,
        target: [0, 0, 1.5],
        goalTheta: -0.62,
        goalPhi: 0.82,
        goalRadius: 118,
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
        layout(location = 2) in vec3 iCenter;   // x, y, base_z (normalized)
        layout(location = 3) in vec3 iScale;    // w, d, h
        layout(location = 4) in vec4 iColor;    // r, g, b, alpha
        layout(location = 5) in vec3 iMeta;     // layerIdx, modId, pulseWeight

        uniform mat4 uViewProj;
        uniform float uExplode;
        uniform float uHighlightMod;
        uniform float uPulseTime;

        out vec3 vNormal;
        out vec3 vLocalPos;
        out vec4 vColor;
        out float vDimmed;

        void main() {
          float layerZ = iMeta.x * uExplode;
          float modId = iMeta.y;
          float pulse = iMeta.z;

          float pulseBoost = pulse > 0.5 ? (0.35 + 0.25 * sin(uPulseTime * 6.0)) : 0.0;
          vec3 scale = iScale;
          // Via pillars (modId < -1.5) stretch vertically with uExplode to bridge adjacent metal layers
          if (modId < -1.5) {
            scale.z = max(0.18, uExplode * 0.92);
          } else if (pulse > 0.5 && iMeta.x < 1.0) {
            scale.z *= (1.0 + 0.45 * pulseBoost);
          }

          vec3 worldPos = vec3(
            iCenter.x + aPos.x * scale.x,
            iCenter.y + aPos.y * scale.y,
            iCenter.z + layerZ + (aPos.z + 0.5) * scale.z
          );

          gl_Position = uViewProj * vec4(worldPos, 1.0);
          vNormal = aNormal;
          vLocalPos = aPos;

          vec4 col = iColor;
          if (pulse > 0.5) {
            col.rgb = mix(col.rgb, vec3(0.95, 1.0, 0.98), pulseBoost * 0.55);
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
      this.uExplode = gl.getUniformLocation(prog, "uExplode");
      this.uHighlightMod = gl.getUniformLocation(prog, "uHighlightMod");
      this.uPulseTime = gl.getUniformLocation(prog, "uPulseTime");

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
      const stride = 13 * 4; // 13 floats per instance
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
      gl.vertexAttribPointer(5, 3, gl.FLOAT, false, stride, 40);
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

    setExplode(val) {
      this.explode = parseFloat(val);
    }

    setHighlightModule(modId) {
      this.highlightMod = modId;
    }

    setActiveCycleState(cycData) {
      this.activeCycleMods.clear();
      if (!cycData) return;
      const cg = cycData.cg || {};
      if (cg.qa) this.activeCycleMods.add(3);
      if (cg.qb) this.activeCycleMods.add(4);
      if (cg.macq) this.activeCycleMods.add(5);
      if (cg.prod_reg) this.activeCycleMods.add(2);
      if (cg.bank0) this.activeCycleMods.add(0);
      if (cg.bank1) this.activeCycleMods.add(1);
      if (cg.drain) this.activeCycleMods.add(8);
      if (cg.prog) this.activeCycleMods.add(9);
      if (cycData.events && cycData.events.some((e) => e.startsWith("port rd"))) {
        this.activeCycleMods.add(6);
      }
      if (cycData.events && cycData.events.some((e) => e.startsWith("fe issue"))) {
        this.activeCycleMods.add(7);
      }
      this.rebuildInstances();
    }

    flyToPreset(preset) {
      if (preset === "iso") {
        this.cam.goalTheta = -0.62;
        this.cam.goalPhi = 0.82;
        this.cam.goalRadius = 118;
        this.cam.goalTarget = [0, 0, 1.5];
        this.highlightMod = -1;
      } else if (preset === "top") {
        // Pure North-Up (+Y = North, +X = East) 2D Floorplan Orientation
        this.cam.goalTheta = 0.0;
        this.cam.goalPhi = 0.01;
        this.cam.goalRadius = 114;
        this.cam.goalTarget = [0, 0, 0];
      } else if (preset === "side") {
        this.cam.goalTheta = -0.18;
        this.cam.goalPhi = 1.44;
        this.cam.goalRadius = 106;
        this.cam.goalTarget = [0, 0, 7];
        if (this.explode < 4.5) this.explode = 5.2;
      }
    }

    flyToModule(modKey) {
      if (!this.chipData || !this.chipData.bookmarks || !this.chipData.bookmarks[modKey]) {
        this.flyToPreset("iso");
        return;
      }
      const bm = this.chipData.bookmarks[modKey];
      const dieW = this.chipData.die.width_um;
      const dieH = this.chipData.die.height_um;
      const nx = ((bm.cx / dieW) - 0.5) * 100;
      const ny = ((bm.cy / dieH) - 0.5) * 100;
      this.cam.goalTarget = [nx, ny, 1.0];
      this.cam.goalRadius = 58;
      this.cam.goalPhi = 0.62;
      this.highlightMod = bm.id;
    }

    rebuildInstances() {
      if (!this.chipData || !this.gl) return;
      const d = this.chipData;
      const dieW = d.die.width_um;
      const dieH = d.die.height_um;
      const scaleX = 100.0 / dieW;
      const scaleY = 100.0 / dieH;
      const isDbu = d.cell_units === "dbu" && d.dbu > 0;
      const cellScaleX = isDbu ? 100.0 / (dieW * d.dbu) : scaleX;
      const cellScaleY = isDbu ? 100.0 / (dieH * d.dbu) : scaleY;

      const list = []; // flat float array (13 floats per box)
      function pushBox(cx, cy, baseZ, sx, sy, sz, r, g, b, a, layerIdx, modId, pulse) {
        list.push(cx, cy, baseZ, sx, sy, sz, r, g, b, a, layerIdx, modId, pulse);
      }

      // 1. Silicon Substrate Base + Exact Core Frame + Die Seal Ring (Z = -1.5 .. 0)
      if (this.vis.substrate) {
        pushBox(0, 0, -1.5, 104, 104, 1.4, 0.04, 0.07, 0.12, 1.0, 0.0, -1, 0);
        // Die Seal Ring
        pushBox(0, -50.6, -0.08, 101.6, 0.45, 0.25, 0.18, 0.34, 0.52, 1.0, 0.0, -1, 0);
        pushBox(0,  50.6, -0.08, 101.6, 0.45, 0.25, 0.22, 0.52, 0.72, 1.0, 0.0, -1, 0); // North edge brighter
        pushBox(-50.6, 0, -0.08, 0.45, 101.6, 0.25, 0.18, 0.34, 0.52, 1.0, 0.0, -1, 0);
        pushBox( 50.6, 0, -0.08, 0.45, 101.6, 0.25, 0.18, 0.34, 0.52, 1.0, 0.0, -1, 0);
        // Exact Core Area Boundary Frame (from 6_final.def ROW bounds)
        if (typeof d.die.core_x0_um === "number") {
          const cx0 = d.die.core_x0_um * scaleX - 50.0;
          const cx1 = d.die.core_x1_um * scaleX - 50.0;
          const cy0 = d.die.core_y0_um * scaleY - 50.0;
          const cy1 = d.die.core_y1_um * scaleY - 50.0;
          const cw = cx1 - cx0, ch = cy1 - cy0;
          const cmx = (cx0 + cx1) * 0.5, cmy = (cy0 + cy1) * 0.5;
          pushBox(cmx, cy0, -0.05, cw, 0.18, 0.12, 0.25, 0.48, 0.68, 0.75, 0.0, -1, 0);
          pushBox(cmx, cy1, -0.05, cw, 0.18, 0.12, 0.25, 0.48, 0.68, 0.75, 0.0, -1, 0);
          pushBox(cx0, cmy, -0.05, 0.18, ch, 0.12, 0.25, 0.48, 0.68, 0.75, 0.0, -1, 0);
          pushBox(cx1, cmy, -0.05, 0.18, ch, 0.12, 0.25, 0.48, 0.68, 0.75, 0.0, -1, 0);
        }
      }

      // 1B. Translucent RTL Macro Floorplan Region Slabs & Borders (p8..p92 of true sequential flops)
      if (this.vis.regions && d.bookmarks) {
        const bms = d.bookmarks;
        for (const k of Object.keys(bms)) {
          const bm = bms[k];
          const m = MODULE_META[bm.id];
          if (!m) continue;
          const inset = k === "mac_bank1" ? 0.42 : 0.0;
          const x0 = bm.x0 * scaleX - 50.0 + inset;
          const x1 = bm.x1 * scaleX - 50.0 - inset;
          const y0 = bm.y0 * scaleY - 50.0 + inset;
          const y1 = bm.y1 * scaleY - 50.0 - inset;
          const cx = (x0 + x1) * 0.5;
          const cy = (y0 + y1) * 0.5;
          const bw = Math.max(3.0, x1 - x0);
          const bh = Math.max(3.0, y1 - y0);
          const [r, g, b] = m.rgb;
          const isPulsing = this.activeCycleMods.has(bm.id) ? 1.0 : 0.0;
          if (k !== "mac_bank1") {
            pushBox(cx, cy, -0.04, bw, bh, 0.06, r * 0.55, g * 0.55, b * 0.55, 0.22, 0.04, bm.id, isPulsing);
          }
          const rail = 0.20;
          pushBox(cx, y0, -0.02, bw, rail, 0.10, r, g, b, 0.75, 0.05, bm.id, isPulsing);
          pushBox(cx, y1, -0.02, bw, rail, 0.10, r, g, b, 0.75, 0.05, bm.id, isPulsing);
          pushBox(x0, cy, -0.02, rail, bh, 0.10, r, g, b, 0.75, 0.05, bm.id, isPulsing);
          pushBox(x1, cy, -0.02, rail, bh, 0.10, r, g, b, 0.75, 0.05, bm.id, isPulsing);
        }
      }

      // 2. FEOL Placed Standard Cells & Lossless Row Fill (100% 1:1 from 6_final.def + PDK LEF)
      if (this.vis.cells) {
        for (let i = 0; i < d.cells.length; i++) {
          const [x, y, w, h, mod, cclass, quad, rawAct] = d.cells[i];
          if (cclass === 4 && !this.vis.filler) continue;

          const act = isDbu ? rawAct * 0.01 : rawAct;
          const cx = (x + w * 0.5) * cellScaleX - 50.0;
          const cy = (y + h * 0.5) * cellScaleY - 50.0;
          const sx = Math.max(0.03, w * cellScaleX * 0.992);
          const sy = Math.max(0.05, h * cellScaleY * 0.955);
          let sz = cclass === 1 ? 0.34 : cclass === 0 ? 0.23 : cclass === 4 ? 0.07 : 0.15;

          let r = 0.3, g = 0.5, b = 0.8, a = 0.97;
          const isPulsing = cclass !== 4 && this.activeCycleMods.has(mod) ? 1.0 : 0.0;

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
              const boost = isPulsing > 0.5 ? Math.min(1.0, act + 0.42) : act * 0.75;
              const hc = heatColor(boost);
              r = hc[0]; g = hc[1]; b = hc[2];
              sz *= (0.85 + boost * 0.75);
            }
          } else if (this.colorMode === "clock_xray") {
            if (cclass === 1) {
              r = 0.55; g = 1.0; b = 0.18; sz = 0.48;
            } else if (cclass === 2) {
              r = 0.0; g = 0.92; b = 1.0; sz = 0.36;
            } else if (mod === 5 || mod === 2) {
              r = 1.0; g = 0.32; b = 0.52; sz = 0.24;
            } else {
              r = 0.10; g = 0.14; b = 0.22; a = 0.32; sz = 0.08;
            }
          }

          pushBox(cx, cy, 0.0, sx, sy, sz, r, g, b, a, 0.18, cclass === 4 ? -1 : mod, isPulsing);
        }
      }

      // 3. Inter-Layer Via Pillars (V1..V6) — modId = -2.0 triggers uExplode Z-scaling in vertex shader
      if (this.vis.vias && d.vias) {
        for (let i = 0; i < d.vias.length; i++) {
          const [vx, vy, lidx, ntype] = d.vias[i];
          if (!this.vis.layers[lidx]) continue;
          if (this.colorMode === "clock_xray" && ntype !== 1 && i % 4 !== 0) continue;
          const cx = vx * scaleX - 50.0;
          const cy = vy * scaleY - 50.0;
          const r = ntype === 1 ? 0.45 : 0.78;
          const g = ntype === 1 ? 0.98 : 0.84;
          const b = ntype === 1 ? 1.00 : 0.92;
          pushBox(cx, cy, 0.15, 0.15, 0.15, 1.0, r, g, b, 0.92, lidx + 1.0, -2.0, ntype === 1 ? 1.0 : 0.0);
        }
      }

      // 4. BEOL Routed Metal Segments (M1..M7 / li1..met5)
      // Alternating orthogonal routing directions:
      // ASAP7: M2=Horiz(X), M3=Vert(Y), M4=Horiz(X), M5=Vert(Y), M6=Horiz(X), M7=Vert(Y)
      // Sky130HD: met1=Horiz(X), met2=Vert(Y), met3=Horiz(X), met4=Vert(Y), met5=Horiz(X)
      if (d.wires) {
        for (let i = 0; i < d.wires.length; i++) {
          const [x1, y1, x2, y2, lidx, ntype] = d.wires[i];
          if (!this.vis.layers[lidx]) continue;
          if (this.colorMode === "clock_xray" && ntype !== 1 && ntype !== 3 && i % 6 !== 0) continue;

          const cx = ((x1 + x2) * 0.5) * scaleX - 50.0;
          const cy = ((y1 + y2) * 0.5) * scaleY - 50.0;
          const dx = Math.abs(x2 - x1) * scaleX;
          const dy = Math.abs(y2 - y1) * scaleY;
          const thick = 0.08 + lidx * 0.022;
          const sx = Math.max(thick, dx);
          const sy = Math.max(thick, dy);
          const sz = 0.10 + lidx * 0.022;

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

          pushBox(cx, cy, 0.2, sx, sy, sz, r, g, b, 0.92, lidx + 1.0, modTag, ntype === 1 ? 1.0 : 0.0);
        }
      }

      // 5. Power Delivery Network Straps (SPECIALNETS VDD / VSS on their exact metal layer index: M5/M6 or met4/met5)
      if (this.vis.pdn && d.pdn) {
        for (let i = 0; i < d.pdn.length; i++) {
          const [x1, y1, x2, y2, wUm, lidx, isVdd] = d.pdn[i];
          const layerIdx = typeof lidx === "number" ? lidx : d.layers.length - 1;
          if (this.vis.layers[layerIdx] === false) continue;
          const cx = ((x1 + x2) * 0.5) * scaleX - 50.0;
          const cy = ((y1 + y2) * 0.5) * scaleY - 50.0;
          const sx = Math.max(wUm * scaleX * 0.85, Math.abs(x2 - x1) * scaleX);
          const sy = Math.max(wUm * scaleY * 0.85, Math.abs(y2 - y1) * scaleY);
          const r = isVdd ? 0.98 : 0.92;
          const g = isVdd ? 0.78 : 0.24;
          const b = isVdd ? 0.18 : 0.38;
          pushBox(cx, cy, 0.22, sx, sy, 0.18, r, g, b, 0.82, layerIdx + 1.05, -1, 0.0);
        }
      }

      // 6. Perimeter I/O Pins (428 OBI Read, OBI Write, Reg Port, Clk/Rst Pins) on their true metal layer & orientation
      if (this.vis.pins && d.pins) {
        for (let i = 0; i < d.pins.length; i++) {
          const p = d.pins[i];
          const cx = p.x * scaleX - 50.0;
          const cy = p.y * scaleY - 50.0;
          const lidx = typeof p.lidx === "number" ? p.lidx : 3;
          const sx = Math.max(0.24, (p.w || 0.3) * scaleX * 1.4);
          const sy = Math.max(0.24, (p.h || 0.3) * scaleY * 1.4);
          let r = 0.0, g = 0.94, b = 1.0;
          if (p.group === "wr_port") { r = 0.96; g = 0.25; b = 0.37; }
          else if (p.group === "reg_port") { r = 0.64; g = 0.90; b = 0.21; }
          else if (p.group === "clk_rst") { r = 1.0; g = 0.85; b = 0.10; }
          pushBox(cx, cy, 0.2, sx, sy, 0.28, r, g, b, 1.0, lidx + 1.02, -1, 0.0);
        }
      }

      const arr = new Float32Array(list);
      this.totalBoxes = arr.length / 13;
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

      const nx = Math.max(0, Math.min(1, (worldX + 50.0) / 100.0));
      const ny = Math.max(0, Math.min(1, (worldY + 50.0) / 100.0));
      const umX = nx * this.chipData.die.width_um;
      const umY = ny * this.chipData.die.height_um;

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
        this.pulsePhase = now * 0.001;
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
        gl.uniform1f(this.uExplode, this.explode);
        gl.uniform1f(this.uHighlightMod, this.highlightMod);
        gl.uniform1f(this.uPulseTime, this.pulsePhase);

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
