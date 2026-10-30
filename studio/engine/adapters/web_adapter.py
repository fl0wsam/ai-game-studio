"""
studio/engine/adapters/web_adapter.py - HTML5 Canvas & Web Export Adapter.
Generates dynamic standalone single-page web games (index.html, game.js, style.css)
tailored to the specific game prompt, title, genre, colors, and mechanics.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from studio.core.models import (
    AssetManifest,
    EngineTarget,
    GameDesignDocument,
    ScriptSpec,
)
from studio.engine.adapters.base import BaseEngineAdapter, EngineProjectManifest
from studio.engine.ast_models import SceneAST


INDEX_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{title} — Autonomous Web Arcade</title>
    <link rel="stylesheet" href="style.css">
    <style>
{inline_css}
    </style>
</head>
<body>
    <div id="arcadeContainer">
        <header id="arcadeHeader">
            <div class="header-left">
                <span class="genre-badge">{genre_badge}</span>
                <h1>{title}</h1>
            </div>
            <div id="arcadeStats">
                <span id="scoreDisplay">Score: 0</span>
                <span id="hpDisplay">HP: {max_hp}</span>
            </div>
        </header>
        <div id="canvasWrapper">
            <canvas id="gameCanvas" width="1280" height="720"></canvas>
        </div>
        <footer id="arcadeFooter">
            <p id="controlsHint">{controls_hint}</p>
        </footer>
    </div>
    <script src="game.js"></script>
    <script>
{inline_js}
    </script>
</body>
</html>
"""

STYLE_CSS_TEMPLATE = """* {{
    margin: 0;
    padding: 0;
    box-sizing: border-box;
}}

body {{
    background-color: {bg_color};
    color: #e2e8f0;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    display: flex;
    justify-content: center;
    align-items: center;
    min-height: 100vh;
    overflow: hidden;
}}

#arcadeContainer {{
    display: flex;
    flex-direction: column;
    align-items: center;
    background: #111422;
    border: 2px solid {primary_color};
    border-radius: 12px;
    box-shadow: 0 10px 40px rgba(0, 0, 0, 0.9), 0 0 25px {primary_color}44;
    overflow: hidden;
    max-width: 1280px;
    width: 95vw;
}}

#arcadeHeader {{
    width: 100%;
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 12px 24px;
    background: #151928;
    border-bottom: 2px solid #232a3e;
}}

.header-left {{
    display: flex;
    align-items: center;
    gap: 12px;
}}

.genre-badge {{
    background: {primary_color}22;
    color: {primary_color};
    border: 1px solid {primary_color}88;
    padding: 2px 8px;
    font-size: 0.75rem;
    font-family: monospace;
    font-weight: bold;
    border-radius: 4px;
    text-transform: uppercase;
}}

#arcadeHeader h1 {{
    font-size: 1.25rem;
    font-weight: 700;
    letter-spacing: 0.05em;
    color: {primary_color};
}}

#arcadeStats {{
    display: flex;
    gap: 20px;
    font-family: "Courier New", monospace;
    font-size: 1.1rem;
    font-weight: bold;
}}

#scoreDisplay {{
    color: #fbbf24;
}}

#hpDisplay {{
    color: #ef4444;
}}

#canvasWrapper {{
    position: relative;
    width: 100%;
    background: #020617;
    display: flex;
    justify-content: center;
    align-items: center;
}}

#gameCanvas {{
    display: block;
    width: 100%;
    height: auto;
    aspect-ratio: 16 / 9;
    image-rendering: pixelated;
}}

#arcadeFooter {{
    width: 100%;
    text-align: center;
    padding: 10px;
    font-size: 0.85rem;
    color: #94a3b8;
    background: #0d101d;
    border-top: 1px solid #1f293d;
}}
"""


def _generate_dynamic_game_js(gdd: GameDesignDocument) -> str:
    """
    Synthesizes custom playable HTML5 Canvas game JavaScript matching
    the exact genre, mechanics, lore, and visual aesthetic of the GDD.
    """
    genre = (gdd.genre or "platformer_2d").lower()
    title = gdd.title or "Arcade Game"
    title_escaped = json.dumps(title)
    lore_escaped = json.dumps(gdd.high_concept or gdd.narrative_lore or "Survive and defeat all objectives!")

    meta = getattr(gdd, "custom_metadata", {}) or {}
    speed_mult = float(meta.get("speed_multiplier", 1.0))
    jump_mult = float(meta.get("jump_multiplier", 1.0))
    hp_override = int(meta.get("hp_override", 0))

    # Base audio synthesizer shared across all games
    audio_code = """
    class RetroAudio {
        constructor() { this.ctx = null; }
        init() {
            if (!this.ctx) {
                const AudioContext = window.AudioContext || window.webkitAudioContext;
                if (AudioContext) this.ctx = new AudioContext();
            }
        }
        beep(freq, duration, type='sine', gainVal=0.1) {
            try {
                this.init();
                if (!this.ctx) return;
                const osc = this.ctx.createOscillator();
                const gain = this.ctx.createGain();
                osc.type = type;
                osc.frequency.setValueAtTime(freq, this.ctx.currentTime);
                gain.gain.setValueAtTime(gainVal, this.ctx.currentTime);
                gain.gain.exponentialRampToValueAtTime(0.001, this.ctx.currentTime + duration);
                osc.connect(gain);
                gain.connect(this.ctx.destination);
                osc.start();
                osc.stop(this.ctx.currentTime + duration);
            } catch(e) {}
        }
        jump() { this.beep(380, 0.12, 'square', 0.12); }
        shoot() { this.beep(820, 0.08, 'sawtooth', 0.14); }
        pickup() { this.beep(523, 0.08, 'triangle', 0.15); setTimeout(() => this.beep(659, 0.12, 'triangle', 0.15), 60); }
        hit() { this.beep(140, 0.22, 'sawtooth', 0.25); }
        slash() { this.beep(420, 0.09, 'triangle', 0.2); }
        dash() { this.beep(300, 0.12, 'sine', 0.18); }
        explosion() { this.beep(95, 0.35, 'square', 0.3); }
        powerup() { this.beep(440, 0.08); setTimeout(() => this.beep(554, 0.08), 70); setTimeout(() => this.beep(659, 0.15), 140); }
        win() { this.beep(520, 0.1, 'sine'); setTimeout(() => this.beep(660, 0.3, 'sine'), 100); }
    }
    const audio = new RetroAudio();
    """

    if "space" in genre or "shooter" in genre:
        game_logic = f"""
    const GAME_TITLE = {title_escaped};
    const GAME_LORE = {lore_escaped};

    const keys = {{}};
    window.addEventListener('keydown', (e) => {{
        keys[e.code] = true;
        audio.init();
        if (e.code === 'KeyR' && (state.gameOver || state.won)) resetGame();
        if (e.code === 'Space' && !state.gameOver && !state.won) shootLaser();
        if ((e.code === 'KeyB' || e.code === 'KeyX') && !state.gameOver && !state.won) triggerBomb();
        if (e.code === 'ShiftLeft' || e.code === 'ShiftRight') triggerDash();
    }});
    window.addEventListener('keyup', (e) => {{ keys[e.code] = false; }});

    let shake = 0;
    function screenShake(amt) {{ shake = Math.max(shake, amt); }}

    const state = {{
        player: {{ x: 640, y: 580, w: 38, h: 42, speed: {7.5 * speed_mult:.1f}, dashCd: 0, invuln: 0 }},
        weaponLevel: 1,
        shield: 0,
        bombs: 1,
        lasers: [],
        enemyBullets: [],
        enemies: [],
        powerups: [],
        particles: [],
        shockwaves: [],
        floaters: [],
        stars: [],
        boss: null,
        score: 0,
        combo: 0,
        hp: {hp_override if hp_override > 0 else 4},
        maxHp: {hp_override if hp_override > 0 else 4},
        lastSpawn: 0,
        gameOver: false,
        won: false
    }};

    for (let i = 0; i < 110; i++) {{
        state.stars.push({{
            x: Math.random() * 1280,
            y: Math.random() * 720,
            size: Math.random() * 2.2 + 0.8,
            speed: Math.random() * 2.5 + 0.6,
            layer: Math.floor(Math.random() * 3)
        }});
    }}

    function addFloater(x, y, text, color='#38bdf8') {{
        state.floaters.push({{ x, y, text, color, life: 1.0 }});
    }}

    function triggerDash() {{
        const p = state.player;
        if (p.dashCd > 0) return;
        p.dashCd = 45;
        p.invuln = 20;
        audio.dash();
        screenShake(4);
        for (let i = 0; i < 12; i++) {{
            state.particles.push({{
                x: p.x + p.w/2, y: p.y + p.h,
                vx: (Math.random() - 0.5) * 6, vy: Math.random() * 8 + 3,
                life: 0.8, color: '#38bdf8', size: 3
            }});
        }}
    }}

    function triggerBomb() {{
        if (state.bombs <= 0) return;
        state.bombs--;
        audio.explosion();
        screenShake(18);
        state.shockwaves.push({{ x: state.player.x + 19, y: state.player.y + 21, r: 10, maxR: 700, color: '#38bdf8' }});
        state.enemies.forEach(en => {{
            en.hp -= 15;
            spawnExplosion(en.x + en.w/2, en.y + en.h/2, '#38bdf8', 16);
        }});
        state.enemyBullets = [];
        addFloater(640, 360, 'NOVA BOMB BLAST!', '#38bdf8');
    }}

    function shootLaser() {{
        const p = state.player;
        audio.shoot();
        if (state.weaponLevel === 1) {{
            state.lasers.push({{ x: p.x + p.w/2 - 2, y: p.y - 12, vx: 0, vy: -15, w: 4, h: 20, color: '#00f0ff' }});
        }} else if (state.weaponLevel === 2) {{
            state.lasers.push({{ x: p.x + 6, y: p.y - 8, vx: 0, vy: -15, w: 5, h: 22, color: '#38bdf8' }});
            state.lasers.push({{ x: p.x + p.w - 11, y: p.y - 8, vx: 0, vy: -15, w: 5, h: 22, color: '#38bdf8' }});
        }} else {{
            state.lasers.push({{ x: p.x + p.w/2 - 3, y: p.y - 12, vx: 0, vy: -16, w: 6, h: 24, color: '#a855f7' }});
            state.lasers.push({{ x: p.x + 4, y: p.y - 6, vx: -3, vy: -15, w: 5, h: 20, color: '#38bdf8' }});
            state.lasers.push({{ x: p.x + p.w - 9, y: p.y - 6, vx: 3, vy: -15, w: 5, h: 20, color: '#38bdf8' }});
        }}
    }}

    function spawnExplosion(x, y, color='#ff007f', count=18) {{
        audio.explosion();
        screenShake(6);
        state.shockwaves.push({{ x, y, r: 5, maxR: 45, color }});
        for (let i = 0; i < count; i++) {{
            const angle = Math.random() * Math.PI * 2;
            const spd = Math.random() * 7 + 2;
            state.particles.push({{
                x, y,
                vx: Math.cos(angle) * spd,
                vy: Math.sin(angle) * spd,
                life: 1.0,
                color,
                size: Math.random() * 3 + 2
            }});
        }}
    }}

    function spawnEnemy() {{
        const r = Math.random();
        if (state.score >= 1200 && !state.boss) {{
            state.boss = {{
                x: 520, y: -160, targetY: 90, w: 240, h: 110,
                hp: 55, maxHp: 55, shootTimer: 0, dir: 1, speed: 2.2
            }};
            addFloater(640, 200, 'WARNING: BOSS DREADNOUGHT!', '#ef4444');
            screenShake(12);
            return;
        }}
        if (r < 0.65) {{
            state.enemies.push({{
                type: 'drone', x: Math.random() * 1180 + 50, y: -40, w: 32, h: 32,
                vx: (Math.random() - 0.5) * 3, vy: Math.random() * 2.2 + 2.8,
                hp: 1, color: '#f43f5e', t: Math.random() * 10
            }});
        }} else {{
            state.enemies.push({{
                type: 'cruiser', x: Math.random() * 1100 + 90, y: -60, w: 54, h: 48,
                vx: 0, vy: 1.6, hp: 4, maxHp: 4, color: '#fbbf24', shootTimer: 30
            }});
        }}
    }}

    function resetGame() {{
        state.player.x = 640;
        state.player.y = 580;
        state.player.dashCd = 0;
        state.player.invuln = 0;
        state.weaponLevel = 1;
        state.shield = 0;
        state.bombs = 1;
        state.lasers = [];
        state.enemyBullets = [];
        state.enemies = [];
        state.powerups = [];
        state.particles = [];
        state.shockwaves = [];
        state.floaters = [];
        state.boss = null;
        state.score = 0;
        state.combo = 0;
        state.hp = state.maxHp;
        state.gameOver = false;
        state.won = false;
        if (scoreDisplay) scoreDisplay.textContent = 'Score: 0';
        if (hpDisplay) hpDisplay.textContent = 'HP: ' + state.hp;
    }}

    function update() {{
        if (state.gameOver || state.won) return;

        const p = state.player;
        if (p.dashCd > 0) p.dashCd--;
        if (p.invuln > 0) p.invuln--;

        // Thruster flame particles
        if (Math.random() < 0.6) {{
            state.particles.push({{
                x: p.x + p.w/2 + (Math.random() - 0.5) * 12,
                y: p.y + p.h - 2,
                vx: (Math.random() - 0.5) * 1.5,
                vy: Math.random() * 4 + 3,
                life: 0.5,
                color: Math.random() < 0.5 ? '#00f0ff' : '#f59e0b',
                size: 3
            }});
        }}

        // Player Movement
        if (keys['ArrowLeft'] || keys['KeyA']) p.x -= p.speed;
        if (keys['ArrowRight'] || keys['KeyD']) p.x += p.speed;
        if (keys['ArrowUp'] || keys['KeyW']) p.y -= p.speed;
        if (keys['ArrowDown'] || keys['KeyS']) p.y += p.speed;

        p.x = Math.max(10, Math.min(1270 - p.w, p.x));
        p.y = Math.max(30, Math.min(710 - p.h, p.y));

        // Background Starfield
        state.stars.forEach(s => {{
            s.y += s.speed;
            if (s.y > 720) {{ s.y = 0; s.x = Math.random() * 1280; }}
        }});

        // Lasers
        for (let i = state.lasers.length - 1; i >= 0; i--) {{
            const l = state.lasers[i];
            l.x += l.vx;
            l.y += l.vy;
            if (l.y < -30 || l.x < -30 || l.x > 1310) state.lasers.splice(i, 1);
        }}

        // Enemy Bullets
        for (let i = state.enemyBullets.length - 1; i >= 0; i--) {{
            const b = state.enemyBullets[i];
            b.x += b.vx;
            b.y += b.vy;
            // Check collision with player
            if (p.invuln <= 0 && b.x > p.x && b.x < p.x + p.w && b.y > p.y && b.y < p.y + p.h) {{
                state.enemyBullets.splice(i, 1);
                if (state.shield > 0) {{
                    state.shield--;
                    audio.hit();
                    screenShake(6);
                    addFloater(p.x, p.y, 'SHIELD ABSORBED!', '#00f0ff');
                }} else {{
                    state.hp--;
                    audio.hit();
                    screenShake(12);
                    p.invuln = 35;
                    if (hpDisplay) hpDisplay.textContent = 'HP: ' + state.hp;
                    if (state.hp <= 0) {{ state.gameOver = true; spawnExplosion(p.x+19, p.y+21, '#00f0ff', 35); }}
                }}
                continue;
            }}
            if (b.y > 740) state.enemyBullets.splice(i, 1);
        }}

        // Powerups falling
        for (let i = state.powerups.length - 1; i >= 0; i--) {{
            const pw = state.powerups[i];
            pw.y += 2.0;
            if (Math.hypot(p.x + p.w/2 - pw.x, p.y + p.h/2 - pw.y) < 32) {{
                audio.powerup();
                if (pw.type === 'weapon') {{
                    state.weaponLevel = Math.min(3, state.weaponLevel + 1);
                    addFloater(p.x, p.y - 10, 'BLASTER UPGRADE!', '#fbbf24');
                }} else if (pw.type === 'shield') {{
                    state.shield = Math.min(3, state.shield + 2);
                    addFloater(p.x, p.y - 10, 'SHIELD ACTIVE (+2)', '#00f0ff');
                }} else if (pw.type === 'bomb') {{
                    state.bombs++;
                    addFloater(p.x, p.y - 10, '+1 NOVA BOMB', '#a855f7');
                }}
                state.powerups.splice(i, 1);
            }} else if (pw.y > 740) {{
                state.powerups.splice(i, 1);
            }}
        }}

        // Spawning
        state.lastSpawn++;
        if (state.lastSpawn > 45) {{
            state.lastSpawn = 0;
            spawnEnemy();
        }}

        // Boss update
        if (state.boss) {{
            const b = state.boss;
            if (b.y < b.targetY) b.y += 1.5;
            else {{
                b.x += b.speed * b.dir;
                if (b.x < 150 || b.x > 890) b.dir *= -1;
                b.shootTimer++;
                if (b.shootTimer > 40) {{
                    b.shootTimer = 0;
                    audio.shoot();
                    for (let angle of [-0.3, 0, 0.3]) {{
                        state.enemyBullets.push({{
                            x: b.x + b.w/2, y: b.y + b.h,
                            vx: Math.sin(angle) * 5, vy: Math.cos(angle) * 5,
                            color: '#ef4444'
                        }});
                    }}
                }}
            }}

            // Laser collision with Boss
            state.lasers.forEach((l, li) => {{
                if (l.x > b.x && l.x < b.x + b.w && l.y > b.y && l.y < b.y + b.h) {{
                    b.hp--;
                    state.lasers.splice(li, 1);
                    state.particles.push({{ x: l.x, y: l.y, vx: (Math.random()-0.5)*4, vy: Math.random()*4, life: 0.4, color: '#f59e0b', size: 2.5 }});
                    if (b.hp <= 0) {{
                        spawnExplosion(b.x + b.w/2, b.y + b.h/2, '#ef4444', 45);
                        state.score += 2500;
                        state.boss = null;
                        state.won = true;
                        audio.win();
                    }}
                }}
            }});
        }}

        // Enemies update
        for (let i = state.enemies.length - 1; i >= 0; i--) {{
            const en = state.enemies[i];
            if (en.type === 'drone') {{
                en.t += 0.05;
                en.x += Math.sin(en.t) * 3;
                en.y += en.vy;
            }} else if (en.type === 'cruiser') {{
                en.y += en.vy;
                en.shootTimer--;
                if (en.shootTimer <= 0) {{
                    en.shootTimer = 55;
                    state.enemyBullets.push({{ x: en.x + en.w/2, y: en.y + en.h, vx: 0, vy: 5, color: '#f43f5e' }});
                }}
            }}

            // Collision with Lasers
            for (let j = state.lasers.length - 1; j >= 0; j--) {{
                const l = state.lasers[j];
                if (l.x > en.x && l.x < en.x + en.w && l.y > en.y && l.y < en.y + en.h) {{
                    en.hp--;
                    state.lasers.splice(j, 1);
                    if (en.hp <= 0) {{
                        spawnExplosion(en.x + en.w/2, en.y + en.h/2, en.color);
                        state.score += (en.type === 'drone' ? 100 : 250);
                        state.combo++;
                        addFloater(en.x, en.y, '+' + (en.type === 'drone' ? 100 : 250), '#fbbf24');
                        if (Math.random() < 0.22) {{
                            const types = ['weapon', 'shield', 'bomb'];
                            state.powerups.push({{ x: en.x, y: en.y, type: types[Math.floor(Math.random()*types.length)] }});
                        }}
                        state.enemies.splice(i, 1);
                        if (scoreDisplay) scoreDisplay.textContent = 'Score: ' + state.score;
                        break;
                    }}
                }}
            }}

            // Collision with Player
            if (p.invuln <= 0 && en.x < p.x + p.w && en.x + en.w > p.x && en.y < p.y + p.h && en.y + en.h > p.y) {{
                spawnExplosion(en.x + en.w/2, en.y + en.h/2, en.color);
                state.enemies.splice(i, 1);
                if (state.shield > 0) {{
                    state.shield--;
                    audio.hit();
                    screenShake(8);
                }} else {{
                    state.hp--;
                    audio.hit();
                    screenShake(14);
                    p.invuln = 40;
                    if (hpDisplay) hpDisplay.textContent = 'HP: ' + state.hp;
                    if (state.hp <= 0) {{ state.gameOver = true; spawnExplosion(p.x+19, p.y+21, '#00f0ff', 35); }}
                }}
            }}

            if (en.y > 740) state.enemies.splice(i, 1);
        }}

        // Shockwaves
        for (let i = state.shockwaves.length - 1; i >= 0; i--) {{
            const sw = state.shockwaves[i];
            sw.r += 6;
            if (sw.r >= sw.maxR) state.shockwaves.splice(i, 1);
        }}

        // Particles
        for (let i = state.particles.length - 1; i >= 0; i--) {{
            const pt = state.particles[i];
            pt.x += pt.vx;
            pt.y += pt.vy;
            pt.life -= 0.025;
            if (pt.life <= 0) state.particles.splice(i, 1);
        }}

        // Floaters
        for (let i = state.floaters.length - 1; i >= 0; i--) {{
            const f = state.floaters[i];
            f.y -= 1.2;
            f.life -= 0.025;
            if (f.life <= 0) state.floaters.splice(i, 1);
        }}
    }}

    function render() {{
        ctx.fillStyle = '#030712';
        ctx.fillRect(0, 0, canvas.width, canvas.height);

        ctx.save();
        if (shake > 0) {{
            ctx.translate((Math.random() - 0.5) * shake, (Math.random() - 0.5) * shake);
            shake *= 0.88;
            if (shake < 0.4) shake = 0;
        }}

        // Starfield
        state.stars.forEach(s => {{
            ctx.fillStyle = s.layer === 0 ? '#475569' : s.layer === 1 ? '#94a3b8' : '#e2e8f0';
            ctx.fillRect(s.x, s.y, s.size, s.size);
        }});

        // Shockwaves
        state.shockwaves.forEach(sw => {{
            ctx.strokeStyle = sw.color;
            ctx.lineWidth = 3;
            ctx.globalAlpha = Math.max(0, 1 - sw.r / sw.maxR);
            ctx.beginPath();
            ctx.arc(sw.x, sw.y, sw.r, 0, Math.PI * 2);
            ctx.stroke();
            ctx.globalAlpha = 1.0;
        }});

        // Lasers with Glow
        ctx.shadowBlur = 10;
        state.lasers.forEach(l => {{
            ctx.fillStyle = l.color;
            ctx.shadowColor = l.color;
            ctx.fillRect(l.x, l.y, l.w, l.h);
        }});

        // Enemy Bullets
        state.enemyBullets.forEach(b => {{
            ctx.fillStyle = b.color;
            ctx.shadowColor = b.color;
            ctx.beginPath();
            ctx.arc(b.x, b.y, 5, 0, Math.PI * 2);
            ctx.fill();
        }});
        ctx.shadowBlur = 0;

        // Powerups
        state.powerups.forEach(pw => {{
            ctx.beginPath();
            ctx.arc(pw.x, pw.y, 14, 0, Math.PI * 2);
            ctx.fillStyle = pw.type === 'weapon' ? '#fbbf24' : pw.type === 'shield' ? '#00f0ff' : '#a855f7';
            ctx.fill();
            ctx.fillStyle = '#000';
            ctx.font = 'bold 11px monospace';
            ctx.textAlign = 'center';
            ctx.fillText(pw.type === 'weapon' ? 'W' : pw.type === 'shield' ? 'S' : 'B', pw.x, pw.y + 4);
        }});

        // Enemies
        state.enemies.forEach(en => {{
            ctx.fillStyle = en.color;
            if (en.type === 'drone') {{
                ctx.beginPath();
                ctx.moveTo(en.x + en.w/2, en.y + en.h);
                ctx.lineTo(en.x, en.y);
                ctx.lineTo(en.x + en.w, en.y);
                ctx.closePath();
                ctx.fill();
            }} else {{
                ctx.fillRect(en.x, en.y, en.w, en.h);
                // Health bar for cruisers
                ctx.fillStyle = '#ef4444';
                ctx.fillRect(en.x, en.y - 6, en.w, 3);
                ctx.fillStyle = '#10b981';
                ctx.fillRect(en.x, en.y - 6, en.w * (en.hp / en.maxHp), 3);
            }}
        }});

        // Boss
        if (state.boss) {{
            const b = state.boss;
            ctx.fillStyle = '#dc2626';
            ctx.fillRect(b.x, b.y, b.w, b.h);
            ctx.fillStyle = '#7f1d1d';
            ctx.fillRect(b.x + 20, b.y + 20, b.w - 40, b.h - 40);

            // Boss Health Bar HUD
            ctx.fillStyle = 'rgba(0,0,0,0.6)';
            ctx.fillRect(340, 20, 600, 24);
            ctx.fillStyle = '#ef4444';
            ctx.fillRect(342, 22, 596 * (b.hp / b.maxHp), 20);
            ctx.fillStyle = '#fff';
            ctx.font = 'bold 12px monospace';
            ctx.textAlign = 'center';
            ctx.fillText('MOTHERSHIP VOID DREADNOUGHT: ' + b.hp + ' / ' + b.maxHp, 640, 36);
        }}

        // Player Ship
        const p = state.player;
        if (p.invuln % 4 < 2) {{
            ctx.fillStyle = '#00f0ff';
            ctx.beginPath();
            ctx.moveTo(p.x + p.w / 2, p.y);
            ctx.lineTo(p.x + p.w, p.y + p.h);
            ctx.lineTo(p.x + p.w / 2, p.y + p.h - 10);
            ctx.lineTo(p.x, p.y + p.h);
            ctx.closePath();
            ctx.fill();

            // Cockpit
            ctx.fillStyle = '#ffffff';
            ctx.fillRect(p.x + p.w/2 - 2, p.y + 12, 4, 10);

            // Shield bubble
            if (state.shield > 0) {{
                ctx.strokeStyle = '#00f0ff';
                ctx.lineWidth = 2;
                ctx.beginPath();
                ctx.arc(p.x + p.w/2, p.y + p.h/2, 32, 0, Math.PI * 2);
                ctx.stroke();
            }}
        }}

        // Particles
        state.particles.forEach(pt => {{
            ctx.fillStyle = pt.color;
            ctx.globalAlpha = Math.max(0, pt.life);
            ctx.fillRect(pt.x, pt.y, pt.size, pt.size);
        }});
        ctx.globalAlpha = 1.0;

        // Floating texts
        state.floaters.forEach(f => {{
            ctx.fillStyle = f.color;
            ctx.globalAlpha = Math.max(0, f.life);
            ctx.font = 'bold 14px monospace';
            ctx.textAlign = 'center';
            ctx.fillText(f.text, f.x, f.y);
        }});
        ctx.globalAlpha = 1.0;

        // HUD Overlay on Canvas
        ctx.fillStyle = '#fbbf24';
        ctx.font = 'bold 16px monospace';
        ctx.textAlign = 'left';
        ctx.fillText('BOMBS: ' + state.bombs + ' [B]', 20, 30);
        ctx.fillText('WEAPON: LVL ' + state.weaponLevel, 20, 52);
        if (state.shield > 0) {{
            ctx.fillStyle = '#00f0ff';
            ctx.fillText('SHIELD: ' + state.shield, 20, 74);
        }}

        if (state.gameOver || state.won) {{
            ctx.fillStyle = 'rgba(0,0,0,0.85)';
            ctx.fillRect(0, 0, canvas.width, canvas.height);
            ctx.textAlign = 'center';
            if (state.won) {{
                ctx.fillStyle = '#10b981';
                ctx.font = 'bold 54px monospace';
                ctx.fillText('MISSION COMPLETE!', canvas.width / 2, canvas.height / 2 - 30);
                ctx.fillStyle = '#f8fafc';
                ctx.font = '22px monospace';
                ctx.fillText('Galaxy Liberated! Final Score: ' + state.score, canvas.width / 2, canvas.height / 2 + 25);
            }} else {{
                ctx.fillStyle = '#ef4444';
                ctx.font = 'bold 54px monospace';
                ctx.fillText('STARSHIP DESTROYED', canvas.width / 2, canvas.height / 2 - 30);
                ctx.fillStyle = '#94a3b8';
                ctx.font = '22px monospace';
                ctx.fillText('Score: ' + state.score + ' | Press [R] to Respawn', canvas.width / 2, canvas.height / 2 + 25);
            }}
        }}

        ctx.restore();
    }}
"""
    elif "rpg" in genre or "dungeon" in genre:
        game_logic = f"""
    const GAME_TITLE = {title_escaped};
    const GAME_LORE = {lore_escaped};

    const keys = {{}};
    window.addEventListener('keydown', (e) => {{
        keys[e.code] = true;
        audio.init();
        if (e.code === 'KeyR' && (state.gameOver || state.won)) resetGame();
        if (e.code === 'Space' && !state.gameOver && !state.won) swordSlash();
        if ((e.code === 'KeyF' || e.code === 'KeyE') && !state.gameOver && !state.won) castFireball();
        if (e.code === 'ShiftLeft' || e.code === 'ShiftRight') dodgeRoll();
    }});
    window.addEventListener('keyup', (e) => {{ keys[e.code] = false; }});

    let shake = 0;
    function screenShake(amt) {{ shake = Math.max(shake, amt); }}

    const state = {{
        hero: {{
            x: 640, y: 480, w: 36, h: 36, speed: {5.4 * speed_mult:.1f},
            facing: 'down', attacking: false, attackTimer: 0,
            dashCd: 0, fireballCd: 0, invuln: 0
        }},
        slashes: [],
        fireballs: [],
        enemies: [],
        enemyProjectiles: [],
        items: [],
        particles: [],
        floaters: [],
        boss: null,
        torches: [
            {{ x: 120, y: 120, light: 1.0 }},
            {{ x: 1160, y: 120, light: 1.0 }},
            {{ x: 120, y: 600, light: 1.0 }},
            {{ x: 1160, y: 600, light: 1.0 }}
        ],
        score: 0,
        hp: {hp_override if hp_override > 0 else 5},
        maxHp: {hp_override if hp_override > 0 else 5},
        gameOver: false,
        won: false
    }};

    function addFloater(x, y, text, color='#10b981') {{
        state.floaters.push({{ x, y, text, color, life: 1.0 }});
    }}

    function spawnBloodSparks(x, y, color='#ef4444', count=12) {{
        for (let i = 0; i < count; i++) {{
            const angle = Math.random() * Math.PI * 2;
            const spd = Math.random() * 5 + 2;
            state.particles.push({{
                x, y,
                vx: Math.cos(angle) * spd,
                vy: Math.sin(angle) * spd,
                life: 0.8,
                color,
                size: Math.random() * 3 + 2
            }});
        }}
    }}

    function swordSlash() {{
        const h = state.hero;
        if (h.attacking) return;
        h.attacking = true;
        h.attackTimer = 16;
        audio.slash();
        screenShake(3);

        const range = 55;
        let sx = h.x + h.w/2;
        let sy = h.y + h.h/2;
        if (h.facing === 'right') sx += range/2;
        if (h.facing === 'left') sx -= range/2;
        if (h.facing === 'down') sy += range/2;
        if (h.facing === 'up') sy -= range/2;

        state.slashes.push({{ x: sx, y: sy, facing: h.facing, life: 12 }});

        // Hit enemies
        state.enemies.forEach(en => {{
            if (Math.hypot(sx - (en.x + en.w/2), sy - (en.y + en.h/2)) < range) {{
                en.hp -= 2;
                audio.hit();
                screenShake(6);
                spawnBloodSparks(en.x + en.w/2, en.y + en.h/2, '#ef4444', 10);
                addFloater(en.x, en.y, '-20 CRIT', '#f59e0b');
                if (h.facing === 'right') en.x += 24;
                if (h.facing === 'left') en.x -= 24;
                if (h.facing === 'down') en.y += 24;
                if (h.facing === 'up') en.y -= 24;
            }}
        }});

        if (state.boss) {{
            const b = state.boss;
            if (Math.hypot(sx - (b.x + b.w/2), sy - (b.y + b.h/2)) < range + 40) {{
                b.hp -= 2;
                audio.hit();
                screenShake(8);
                spawnBloodSparks(b.x + b.w/2, b.y + b.h/2, '#f59e0b', 12);
                addFloater(b.x + b.w/2, b.y, '-20', '#fbbf24');
            }}
        }}
    }}

    function castFireball() {{
        const h = state.hero;
        if (h.fireballCd > 0) return;
        h.fireballCd = 35;
        audio.shoot();
        screenShake(4);

        let vx = 0, vy = 0;
        if (h.facing === 'right') vx = 11;
        else if (h.facing === 'left') vx = -11;
        else if (h.facing === 'down') vy = 11;
        else vy = -11;

        state.fireballs.push({{
            x: h.x + h.w/2, y: h.y + h.h/2, vx, vy, life: 70
        }});
        addFloater(h.x, h.y - 10, 'FIREBALL!', '#f59e0b');
    }}

    function dodgeRoll() {{
        const h = state.hero;
        if (h.dashCd > 0) return;
        h.dashCd = 50;
        h.invuln = 22;
        audio.dash();
        screenShake(3);

        let dx = 0, dy = 0;
        if (h.facing === 'right') dx = 16;
        if (h.facing === 'left') dx = -16;
        if (h.facing === 'down') dy = 16;
        if (h.facing === 'up') dy = -16;

        h.x += dx * 4;
        h.y += dy * 4;
        h.x = Math.max(50, Math.min(1230 - h.w, h.x));
        h.y = Math.max(70, Math.min(670 - h.h, h.y));

        for (let i = 0; i < 8; i++) {{
            state.particles.push({{
                x: h.x + h.w/2, y: h.y + h.h/2,
                vx: (Math.random()-0.5)*4, vy: (Math.random()-0.5)*4,
                life: 0.5, color: '#10b981', size: 3
            }});
        }}
    }}

    function initEnemies() {{
        state.enemies = [
            {{ type: 'skeleton', x: 260, y: 220, w: 32, h: 32, hp: 4, speed: 2.1, color: '#e2e8f0' }},
            {{ type: 'skeleton', x: 920, y: 220, w: 32, h: 32, hp: 4, speed: 2.1, color: '#e2e8f0' }},
            {{ type: 'archer', x: 260, y: 560, w: 30, h: 30, hp: 3, speed: 1.6, shootCd: 50, color: '#84cc16' }},
            {{ type: 'archer', x: 920, y: 560, w: 30, h: 30, hp: 3, speed: 1.6, shootCd: 80, color: '#84cc16' }}
        ];
        state.boss = {{
            name: 'DEMON OVERLORD', x: 570, y: 160, w: 90, h: 80,
            hp: 40, maxHp: 40, speed: 1.4, attackCd: 70, color: '#dc2626'
        }};
    }}
    initEnemies();

    function resetGame() {{
        state.hero.x = 640;
        state.hero.y = 480;
        state.hero.hp = state.maxHp;
        state.hero.attacking = false;
        state.hero.dashCd = 0;
        state.hero.invuln = 0;
        state.hero.fireballCd = 0;
        state.slashes = [];
        state.fireballs = [];
        state.enemyProjectiles = [];
        state.items = [];
        state.particles = [];
        state.floaters = [];
        state.score = 0;
        state.gameOver = false;
        state.won = false;
        initEnemies();
        if (scoreDisplay) scoreDisplay.textContent = 'Score: 0';
        if (hpDisplay) hpDisplay.textContent = 'HP: ' + state.maxHp;
    }}

    function update() {{
        if (state.gameOver || state.won) return;

        const h = state.hero;
        if (h.dashCd > 0) h.dashCd--;
        if (h.fireballCd > 0) h.fireballCd--;
        if (h.invuln > 0) h.invuln--;

        if (h.attackTimer > 0) {{
            h.attackTimer--;
            if (h.attackTimer <= 0) h.attacking = false;
        }}

        for (let i = state.slashes.length - 1; i >= 0; i--) {{
            state.slashes[i].life--;
            if (state.slashes[i].life <= 0) state.slashes.splice(i, 1);
        }}

        let moving = false;
        if (keys['ArrowLeft'] || keys['KeyA']) {{ h.x -= h.speed; h.facing = 'left'; moving = true; }}
        if (keys['ArrowRight'] || keys['KeyD']) {{ h.x += h.speed; h.facing = 'right'; moving = true; }}
        if (keys['ArrowUp'] || keys['KeyW']) {{ h.y -= h.speed; h.facing = 'up'; moving = true; }}
        if (keys['ArrowDown'] || keys['KeyS']) {{ h.y += h.speed; h.facing = 'down'; moving = true; }}

        h.x = Math.max(50, Math.min(1230 - h.w, h.x));
        h.y = Math.max(70, Math.min(670 - h.h, h.y));

        for (let i = state.fireballs.length - 1; i >= 0; i--) {{
            const fb = state.fireballs[i];
            fb.x += fb.vx;
            fb.y += fb.vy;
            fb.life--;

            state.particles.push({{
                x: fb.x, y: fb.y,
                vx: (Math.random()-0.5)*2, vy: (Math.random()-0.5)*2,
                life: 0.35, color: '#f59e0b', size: 3.5
            }});

            let hit = false;
            state.enemies.forEach(en => {{
                if (Math.hypot(fb.x - (en.x + en.w/2), fb.y - (en.y + en.h/2)) < 36) {{
                    en.hp -= 3;
                    hit = true;
                    spawnBloodSparks(fb.x, fb.y, '#f59e0b', 16);
                    audio.explosion();
                    screenShake(7);
                    addFloater(en.x, en.y, '-35 FIRE', '#f59e0b');
                }}
            }});

            if (state.boss && Math.hypot(fb.x - (state.boss.x + state.boss.w/2), fb.y - (state.boss.y + state.boss.h/2)) < 55) {{
                state.boss.hp -= 3;
                hit = true;
                spawnBloodSparks(fb.x, fb.y, '#f59e0b', 20);
                audio.explosion();
                screenShake(9);
                addFloater(state.boss.x, state.boss.y, '-35 FIRE', '#fbbf24');
            }}

            if (hit || fb.life <= 0 || fb.x < 40 || fb.x > 1240 || fb.y < 50 || fb.y > 680) {{
                state.fireballs.splice(i, 1);
            }}
        }}

        for (let i = state.enemyProjectiles.length - 1; i >= 0; i--) {{
            const ep = state.enemyProjectiles[i];
            ep.x += ep.vx;
            ep.y += ep.vy;

            if (h.invuln <= 0 && Math.hypot(ep.x - (h.x + h.w/2), ep.y - (h.y + h.h/2)) < 22) {{
                state.hero.hp--;
                h.invuln = 35;
                audio.hit();
                screenShake(10);
                addFloater(h.x, h.y, '-1 HP', '#ef4444');
                state.enemyProjectiles.splice(i, 1);
                if (hpDisplay) hpDisplay.textContent = 'HP: ' + state.hero.hp;
                if (state.hero.hp <= 0) state.gameOver = true;
                continue;
            }}

            ep.life--;
            if (ep.life <= 0) state.enemyProjectiles.splice(i, 1);
        }}

        for (let i = state.enemies.length - 1; i >= 0; i--) {{
            const en = state.enemies[i];
            const dist = Math.hypot(h.x - en.x, h.y - en.y);

            if (en.type === 'skeleton') {{
                if (dist < 420 && dist > 15) {{
                    const angle = Math.atan2((h.y + h.h/2) - (en.y + en.h/2), (h.x + h.w/2) - (en.x + en.w/2));
                    en.x += Math.cos(angle) * en.speed;
                    en.y += Math.sin(angle) * en.speed;
                }}
                if (h.invuln <= 0 && dist < 32) {{
                    state.hero.hp--;
                    h.invuln = 35;
                    audio.hit();
                    screenShake(12);
                    addFloater(h.x, h.y, '-1 HP', '#ef4444');
                    if (hpDisplay) hpDisplay.textContent = 'HP: ' + state.hero.hp;
                    if (state.hero.hp <= 0) state.gameOver = true;
                }}
            }} else if (en.type === 'archer') {{
                if (dist < 260) {{
                    const angle = Math.atan2((en.y - h.y), (en.x - h.x));
                    en.x += Math.cos(angle) * en.speed;
                    en.y += Math.sin(angle) * en.speed;
                }}
                en.shootCd--;
                if (en.shootCd <= 0) {{
                    en.shootCd = 75;
                    const angle = Math.atan2((h.y + h.h/2) - (en.y + en.h/2), (h.x + h.w/2) - (en.x + en.w/2));
                    state.enemyProjectiles.push({{
                        x: en.x + en.w/2, y: en.y + en.h/2,
                        vx: Math.cos(angle) * 5.5, vy: Math.sin(angle) * 5.5,
                        life: 120
                    }});
                }}
            }}

            if (en.hp <= 0) {{
                spawnBloodSparks(en.x + en.w/2, en.y + en.h/2, en.color, 24);
                state.score += 200;
                addFloater(en.x, en.y, '+200 XP', '#fbbf24');
                if (Math.random() < 0.45) {{
                    state.items.push({{
                        x: en.x, y: en.y,
                        type: Math.random() < 0.4 ? 'potion' : 'coin'
                    }});
                }}
                state.enemies.splice(i, 1);
                if (scoreDisplay) scoreDisplay.textContent = 'Score: ' + state.score;
            }}
        }}

        if (state.boss) {{
            const b = state.boss;
            const dist = Math.hypot(h.x - b.x, h.y - b.y);
            const angle = Math.atan2(h.y - b.y, h.x - b.x);

            b.x += Math.cos(angle) * b.speed;
            b.y += Math.sin(angle) * b.speed;

            b.attackCd--;
            if (b.attackCd <= 0) {{
                b.attackCd = 80;
                audio.explosion();
                screenShake(10);
                for (let i = 0; i < 8; i++) {{
                    const a = (Math.PI * 2 / 8) * i;
                    state.enemyProjectiles.push({{
                        x: b.x + b.w/2, y: b.y + b.h/2,
                        vx: Math.cos(a) * 4.5, vy: Math.sin(a) * 4.5,
                        life: 80
                    }});
                }}
                addFloater(b.x + b.w/2, b.y, 'STOMP SHOCKWAVE!', '#ef4444');
            }}

            if (h.invuln <= 0 && dist < 50) {{
                state.hero.hp -= 2;
                h.invuln = 45;
                audio.hit();
                screenShake(16);
                addFloater(h.x, h.y, '-2 HP HEAVY HIT', '#ef4444');
                if (hpDisplay) hpDisplay.textContent = 'HP: ' + state.hero.hp;
                if (state.hero.hp <= 0) state.gameOver = true;
            }}

            if (b.hp <= 0) {{
                spawnBloodSparks(b.x + b.w/2, b.y + b.h/2, '#ef4444', 50);
                state.score += 2000;
                state.boss = null;
                state.won = true;
                audio.win();
            }}
        }}

        for (let i = state.items.length - 1; i >= 0; i--) {{
            const it = state.items[i];
            if (Math.hypot(h.x + h.w/2 - it.x, h.y + h.h/2 - it.y) < 32) {{
                audio.pickup();
                if (it.type === 'potion') {{
                    state.hero.hp = Math.min(state.maxHp, state.hero.hp + 2);
                    addFloater(h.x, h.y - 12, '+2 HP HEALED', '#10b981');
                    if (hpDisplay) hpDisplay.textContent = 'HP: ' + state.hero.hp;
                }} else {{
                    state.score += 150;
                    addFloater(h.x, h.y - 12, '+150 GOLD', '#fbbf24');
                    if (scoreDisplay) scoreDisplay.textContent = 'Score: ' + state.score;
                }}
                state.items.splice(i, 1);
            }}
        }}

        for (let i = state.particles.length - 1; i >= 0; i--) {{
            const pt = state.particles[i];
            pt.x += pt.vx;
            pt.y += pt.vy;
            pt.life -= 0.025;
            if (pt.life <= 0) state.particles.splice(i, 1);
        }}

        for (let i = state.floaters.length - 1; i >= 0; i--) {{
            const f = state.floaters[i];
            f.y -= 1.0;
            f.life -= 0.025;
            if (f.life <= 0) state.floaters.splice(i, 1);
        }}
    }}

    function render() {{
        ctx.fillStyle = '#0a0d18';
        ctx.fillRect(0, 0, canvas.width, canvas.height);

        ctx.save();
        if (shake > 0) {{
            ctx.translate((Math.random() - 0.5) * shake, (Math.random() - 0.5) * shake);
            shake *= 0.88;
            if (shake < 0.4) shake = 0;
        }}

        // Dungeon Floor
        const tileSize = 60;
        for (let x = 40; x < 1240; x += tileSize) {{
            for (let y = 60; y < 680; y += tileSize) {{
                const isEven = ((x / tileSize) + (y / tileSize)) % 2 === 0;
                ctx.fillStyle = isEven ? '#0f172a' : '#0b1120';
                ctx.fillRect(x, y, tileSize, tileSize);
                ctx.strokeStyle = '#1e293b';
                ctx.lineWidth = 0.5;
                ctx.strokeRect(x, y, tileSize, tileSize);
            }}
        }}

        // Dungeon Walls
        ctx.fillStyle = '#1e293b';
        ctx.fillRect(0, 0, 1280, 60);
        ctx.fillRect(0, 680, 1280, 40);
        ctx.fillRect(0, 0, 40, 720);
        ctx.fillRect(1240, 0, 40, 720);

        // Torchlight
        state.torches.forEach(t => {{
            const grad = ctx.createRadialGradient(t.x, t.y, 10, t.x, t.y, 160);
            grad.addColorStop(0, 'rgba(245, 158, 11, 0.25)');
            grad.addColorStop(1, 'rgba(245, 158, 11, 0)');
            ctx.fillStyle = grad;
            ctx.beginPath();
            ctx.arc(t.x, t.y, 160, 0, Math.PI*2);
            ctx.fill();

            ctx.fillStyle = '#f59e0b';
            ctx.beginPath();
            ctx.arc(t.x, t.y, 6 + Math.random()*2, 0, Math.PI*2);
            ctx.fill();
        }});

        // Items
        state.items.forEach(it => {{
            ctx.beginPath();
            ctx.arc(it.x, it.y, 10, 0, Math.PI*2);
            ctx.fillStyle = it.type === 'potion' ? '#10b981' : '#fbbf24';
            ctx.fill();
            ctx.fillStyle = '#fff';
            ctx.font = 'bold 9px monospace';
            ctx.textAlign = 'center';
            ctx.fillText(it.type === 'potion' ? 'HP' : '$', it.x, it.y + 3);
        }});

        // Fireballs
        state.fireballs.forEach(fb => {{
            ctx.fillStyle = '#f97316';
            ctx.shadowColor = '#f97316';
            ctx.shadowBlur = 12;
            ctx.beginPath();
            ctx.arc(fb.x, fb.y, 8, 0, Math.PI*2);
            ctx.fill();
            ctx.shadowBlur = 0;
        }});

        // Enemy Projectiles
        ctx.fillStyle = '#ef4444';
        state.enemyProjectiles.forEach(ep => {{
            ctx.beginPath();
            ctx.arc(ep.x, ep.y, 5, 0, Math.PI*2);
            ctx.fill();
        }});

        // Enemies
        state.enemies.forEach(en => {{
            ctx.fillStyle = en.color;
            ctx.fillRect(en.x, en.y, en.w, en.h);

            ctx.fillStyle = '#ef4444';
            ctx.fillRect(en.x, en.y - 6, en.w, 3);
            ctx.fillStyle = '#10b981';
            ctx.fillRect(en.x, en.y - 6, en.w * (en.hp / 4), 3);
        }});

        // Boss
        if (state.boss) {{
            const b = state.boss;
            ctx.fillStyle = b.color;
            ctx.fillRect(b.x, b.y, b.w, b.h);

            ctx.fillStyle = '#fef08a';
            ctx.fillRect(b.x + 20, b.y + 25, 10, 8);
            ctx.fillRect(b.x + b.w - 30, b.y + 25, 10, 8);

            ctx.fillStyle = 'rgba(0,0,0,0.7)';
            ctx.fillRect(340, 16, 600, 24);
            ctx.fillStyle = '#dc2626';
            ctx.fillRect(342, 18, 596 * (b.hp / b.maxHp), 20);
            ctx.fillStyle = '#fff';
            ctx.font = 'bold 12px monospace';
            ctx.textAlign = 'center';
            ctx.fillText(b.name + ' — ' + b.hp + ' / ' + b.maxHp, 640, 32);
        }}

        // Hero
        const h = state.hero;
        if (h.invuln % 4 < 2) {{
            ctx.fillStyle = '#10b981';
            ctx.fillRect(h.x, h.y, h.w, h.h);

            ctx.fillStyle = '#022c22';
            ctx.fillRect(h.x + 6, h.y + 8, h.w - 12, 6);

            ctx.fillStyle = '#ef4444';
            ctx.fillRect(h.x, h.y - 8, h.w, 4);
            ctx.fillStyle = '#10b981';
            ctx.fillRect(h.x, h.y - 8, h.w * (state.hero.hp / state.maxHp), 4);
        }}

        // Sword Slash Arc
        state.slashes.forEach(s => {{
            ctx.save();
            ctx.strokeStyle = '#38bdf8';
            ctx.lineWidth = 4;
            ctx.shadowColor = '#38bdf8';
            ctx.shadowBlur = 12;
            ctx.beginPath();
            ctx.arc(s.x, s.y, 28, 0, Math.PI);
            ctx.stroke();
            ctx.restore();
        }});

        // Particles
        state.particles.forEach(pt => {{
            ctx.fillStyle = pt.color;
            ctx.globalAlpha = Math.max(0, pt.life);
            ctx.fillRect(pt.x, pt.y, pt.size, pt.size);
        }});
        ctx.globalAlpha = 1.0;

        // Floaters
        state.floaters.forEach(f => {{
            ctx.fillStyle = f.color;
            ctx.globalAlpha = Math.max(0, f.life);
            ctx.font = 'bold 13px monospace';
            ctx.textAlign = 'center';
            ctx.fillText(f.text, f.x, f.y);
        }});
        ctx.globalAlpha = 1.0;

        // Abilities HUD
        ctx.fillStyle = '#94a3b8';
        ctx.font = '12px monospace';
        ctx.textAlign = 'left';
        ctx.fillText('[Space] Sword Slash  |  [F] Fireball (' + (h.fireballCd === 0 ? 'READY' : Math.ceil(h.fireballCd/10)) + ')  |  [Shift] Dodge Roll', 60, 48);

        if (state.gameOver || state.won) {{
            ctx.fillStyle = 'rgba(0,0,0,0.85)';
            ctx.fillRect(0, 0, canvas.width, canvas.height);
            ctx.textAlign = 'center';
            if (state.won) {{
                ctx.fillStyle = '#10b981';
                ctx.font = 'bold 52px monospace';
                ctx.fillText('DUNGEON CONQUERED!', canvas.width / 2, canvas.height / 2 - 30);
                ctx.fillStyle = '#fff';
                ctx.font = '22px monospace';
                ctx.fillText('Demon Overlord Slain! Final Score: ' + state.score, canvas.width / 2, canvas.height / 2 + 25);
            }} else {{
                ctx.fillStyle = '#ef4444';
                ctx.font = 'bold 52px monospace';
                ctx.fillText('HERO FALLEN', canvas.width / 2, canvas.height / 2 - 30);
                ctx.fillStyle = '#94a3b8';
                ctx.font = '22px monospace';
                ctx.fillText('Score: ' + state.score + ' | Press [R] to Rise Again', canvas.width / 2, canvas.height / 2 + 25);
            }}
        }}

        ctx.restore();
    }}
"""
    elif "puzzle" in genre:
        game_logic = f"""
    const GAME_TITLE = {title_escaped};
    const GAME_LORE = {lore_escaped};

    const COLS = 8;
    const ROWS = 8;
    const GEM_SIZE = 64;
    const BOARD_X = (1280 - COLS * GEM_SIZE) / 2;
    const BOARD_Y = 100;
    const GEM_COLORS = ['#ef4444', '#38bdf8', '#10b981', '#f59e0b', '#a855f7'];

    let selected = null;
    let isSwapping = false;

    window.addEventListener('keydown', (e) => {{
        audio.init();
        if (e.code === 'KeyR') resetGame();
    }});

    const state = {{
        grid: [],
        particles: [],
        floaters: [],
        score: 0,
        movesLeft: 25,
        targetScore: 2500,
        combo: 0,
        gameOver: false,
        won: false
    }};

    function addFloater(x, y, text, color='#fbbf24') {{
        state.floaters.push({{ x, y, text, color, life: 1.0 }});
    }}

    function initGrid() {{
        state.grid = [];
        for (let r = 0; r < ROWS; r++) {{
            const row = [];
            for (let c = 0; c < COLS; c++) {{
                row.push(Math.floor(Math.random() * GEM_COLORS.length));
            }}
            state.grid.push(row);
        }}
        clearMatches(false);
    }}

    function resetGame() {{
        state.score = 0;
        state.movesLeft = 25;
        state.combo = 0;
        state.gameOver = false;
        state.won = false;
        state.particles = [];
        state.floaters = [];
        selected = null;
        initGrid();
        if (scoreDisplay) scoreDisplay.textContent = 'Score: 0';
        if (hpDisplay) hpDisplay.textContent = 'Moves: 25';
    }}

    function findMatches() {{
        const matched = [];
        for (let r = 0; r < ROWS; r++) {{
            for (let c = 0; c < COLS - 2; c++) {{
                const val = state.grid[r][c];
                if (val !== -1 && val === state.grid[r][c+1] && val === state.grid[r][c+2]) {{
                    matched.push({{ r, c }}, {{ r, c: c+1 }}, {{ r, c: c+2 }});
                }}
            }}
        }}
        for (let c = 0; c < COLS; c++) {{
            for (let r = 0; r < ROWS - 2; r++) {{
                const val = state.grid[r][c];
                if (val !== -1 && val === state.grid[r+1][c] && val === state.grid[r+2][c]) {{
                    matched.push({{ r, c }}, {{ r: r+1, c }}, {{ r: r+2, c }});
                }}
            }}
        }}
        return matched;
    }}

    function clearMatches(giveScore=true) {{
        const matches = findMatches();
        if (matches.length === 0) return false;

        const unique = new Set();
        matches.forEach(m => unique.add(`${{m.r}},${{m.c}}`));

        if (giveScore) {{
            state.combo++;
            audio.pickup();
            const earned = unique.size * 100 * state.combo;
            state.score += earned;
            addFloater(640, 70, '+' + earned + ' (COMBO x' + state.combo + ')', '#fbbf24');
            if (scoreDisplay) scoreDisplay.textContent = 'Score: ' + state.score;
            if (state.score >= state.targetScore) {{
                state.won = true;
                audio.win();
            }}
        }}

        unique.forEach(key => {{
            const [r, c] = key.split(',').map(Number);
            const gx = BOARD_X + c * GEM_SIZE + GEM_SIZE / 2;
            const gy = BOARD_Y + r * GEM_SIZE + GEM_SIZE / 2;
            const color = GEM_COLORS[state.grid[r][c]] || '#38bdf8';
            for (let i = 0; i < 10; i++) {{
                state.particles.push({{
                    x: gx, y: gy,
                    vx: (Math.random() - 0.5) * 6, vy: (Math.random() - 0.5) * 6,
                    life: 0.6, color, size: 3.5
                }});
            }}
            state.grid[r][c] = -1;
        }});

        for (let c = 0; c < COLS; c++) {{
            let emptyRow = ROWS - 1;
            for (let r = ROWS - 1; r >= 0; r--) {{
                if (state.grid[r][c] !== -1) {{
                    const val = state.grid[r][c];
                    state.grid[r][c] = -1;
                    state.grid[emptyRow][c] = val;
                    emptyRow--;
                }}
            }}
            while (emptyRow >= 0) {{
                state.grid[emptyRow][c] = Math.floor(Math.random() * GEM_COLORS.length);
                emptyRow--;
            }}
        }}

        setTimeout(() => clearMatches(giveScore), 250);
        return true;
    }}

    canvas.addEventListener('click', (e) => {{
        if (state.gameOver || state.won || isSwapping) return;
        audio.init();
        const rect = canvas.getBoundingClientRect();
        const mx = (e.clientX - rect.left) * (canvas.width / rect.width);
        const my = (e.clientY - rect.top) * (canvas.height / rect.height);

        const c = Math.floor((mx - BOARD_X) / GEM_SIZE);
        const r = Math.floor((my - BOARD_Y) / GEM_SIZE);

        if (c < 0 || c >= COLS || r < 0 || r >= ROWS) return;

        if (!selected) {{
            selected = {{ r, c }};
            audio.jump();
        }} else {{
            const dist = Math.abs(selected.r - r) + Math.abs(selected.c - c);
            if (dist === 1) {{
                isSwapping = true;
                const temp = state.grid[r][c];
                state.grid[r][c] = state.grid[selected.r][selected.c];
                state.grid[selected.r][selected.c] = temp;

                state.movesLeft--;
                if (hpDisplay) hpDisplay.textContent = 'Moves: ' + state.movesLeft;

                const matched = clearMatches(true);
                if (!matched) {{
                    setTimeout(() => {{
                        const rev = state.grid[r][c];
                        state.grid[r][c] = state.grid[selected.r][selected.c];
                        state.grid[selected.r][selected.c] = rev;
                        isSwapping = false;
                        audio.hit();
                    }}, 200);
                }} else {{
                    isSwapping = false;
                }}

                if (state.movesLeft <= 0 && state.score < state.targetScore) {{
                    state.gameOver = true;
                    audio.hit();
                }}
            }}
            selected = null;
        }}
    }});

    initGrid();

    function update() {{
        for (let i = state.particles.length - 1; i >= 0; i--) {{
            const pt = state.particles[i];
            pt.x += pt.vx;
            pt.y += pt.vy;
            pt.life -= 0.025;
            if (pt.life <= 0) state.particles.splice(i, 1);
        }}

        for (let i = state.floaters.length - 1; i >= 0; i--) {{
            const f = state.floaters[i];
            f.y -= 1.0;
            f.life -= 0.025;
            if (f.life <= 0) state.floaters.splice(i, 1);
        }}
    }}

    function render() {{
        ctx.fillStyle = '#080a14';
        ctx.fillRect(0, 0, canvas.width, canvas.height);

        ctx.fillStyle = '#0f172a';
        ctx.fillRect(BOARD_X - 10, BOARD_Y - 10, COLS * GEM_SIZE + 20, ROWS * GEM_SIZE + 20);
        ctx.strokeStyle = '#38bdf8';
        ctx.lineWidth = 2;
        ctx.strokeRect(BOARD_X - 10, BOARD_Y - 10, COLS * GEM_SIZE + 20, ROWS * GEM_SIZE + 20);

        for (let r = 0; r < ROWS; r++) {{
            for (let c = 0; c < COLS; c++) {{
                const gx = BOARD_X + c * GEM_SIZE;
                const gy = BOARD_Y + r * GEM_SIZE;

                ctx.strokeStyle = '#1e293b';
                ctx.lineWidth = 1;
                ctx.strokeRect(gx, gy, GEM_SIZE, GEM_SIZE);

                const val = state.grid[r][c];
                if (val !== -1) {{
                    const color = GEM_COLORS[val];
                    ctx.save();
                    ctx.fillStyle = color;
                    ctx.shadowColor = color;
                    ctx.shadowBlur = (selected && selected.r === r && selected.c === c) ? 20 : 6;

                    ctx.beginPath();
                    ctx.moveTo(gx + GEM_SIZE / 2, gy + 8);
                    ctx.lineTo(gx + GEM_SIZE - 8, gy + GEM_SIZE / 2);
                    ctx.lineTo(gx + GEM_SIZE / 2, gy + GEM_SIZE - 8);
                    ctx.lineTo(gx + 8, gy + GEM_SIZE / 2);
                    ctx.closePath();
                    ctx.fill();

                    ctx.fillStyle = 'rgba(255,255,255,0.4)';
                    ctx.beginPath();
                    ctx.moveTo(gx + GEM_SIZE / 2, gy + 12);
                    ctx.lineTo(gx + GEM_SIZE / 2 + 10, gy + GEM_SIZE / 2);
                    ctx.lineTo(gx + GEM_SIZE / 2, gy + GEM_SIZE / 2);
                    ctx.closePath();
                    ctx.fill();

                    ctx.restore();
                }}

                if (selected && selected.r === r && selected.c === c) {{
                    ctx.strokeStyle = '#ffffff';
                    ctx.lineWidth = 3;
                    ctx.strokeRect(gx + 4, gy + 4, GEM_SIZE - 8, GEM_SIZE - 8);
                }}
            }}
        }}

        state.particles.forEach(pt => {{
            ctx.fillStyle = pt.color;
            ctx.globalAlpha = Math.max(0, pt.life);
            ctx.fillRect(pt.x, pt.y, pt.size, pt.size);
        }});
        ctx.globalAlpha = 1.0;

        state.floaters.forEach(f => {{
            ctx.fillStyle = f.color;
            ctx.globalAlpha = Math.max(0, f.life);
            ctx.font = 'bold 15px monospace';
            ctx.textAlign = 'center';
            ctx.fillText(f.text, f.x, f.y);
        }});
        ctx.globalAlpha = 1.0;

        ctx.fillStyle = '#94a3b8';
        ctx.font = '14px monospace';
        ctx.textAlign = 'center';
        ctx.fillText('TARGET SCORE: ' + state.targetScore + '  |  REMAINING MOVES: ' + state.movesLeft, 640, 50);

        if (state.gameOver || state.won) {{
            ctx.fillStyle = 'rgba(0,0,0,0.85)';
            ctx.fillRect(0, 0, canvas.width, canvas.height);
            ctx.textAlign = 'center';
            if (state.won) {{
                ctx.fillStyle = '#10b981';
                ctx.font = 'bold 52px monospace';
                ctx.fillText('PUZZLE MASTERED!', canvas.width / 2, canvas.height / 2 - 30);
                ctx.fillStyle = '#fff';
                ctx.font = '22px monospace';
                ctx.fillText('Goal reached! Final Score: ' + state.score, canvas.width / 2, canvas.height / 2 + 25);
            }} else {{
                ctx.fillStyle = '#ef4444';
                ctx.font = 'bold 52px monospace';
                ctx.fillText('OUT OF MOVES', canvas.width / 2, canvas.height / 2 - 30);
                ctx.fillStyle = '#94a3b8';
                ctx.font = '22px monospace';
                ctx.fillText('Score: ' + state.score + ' | Press [R] to Try Again', canvas.width / 2, canvas.height / 2 + 25);
            }}
        }}
    }}
"""
    else:
        # Platformer 2D
        game_logic = f"""
    const GAME_TITLE = {title_escaped};
    const GAME_LORE = {lore_escaped};

    const keys = {{}};
    window.addEventListener('keydown', (e) => {{
        keys[e.code] = true;
        audio.init();
        if (e.code === 'KeyR' && (state.gameOver || state.won)) resetGame();
        if ((e.code === 'Space' || e.code === 'KeyW' || e.code === 'ArrowUp') && !state.gameOver && !state.won) {{
            playerJump();
        }}
        if ((e.code === 'ShiftLeft' || e.code === 'ShiftRight' || e.code === 'KeyC') && !state.gameOver && !state.won) {{
            playerDash();
        }}
    }});
    window.addEventListener('keyup', (e) => {{ keys[e.code] = false; }});

    let shake = 0;
    function screenShake(amt) {{ shake = Math.max(shake, amt); }}

    const state = {{
        player: {{
            x: 80, y: 550, w: 32, h: 44, vx: 0, vy: 0,
            onGround: false, onWall: 0, jumpsLeft: 2,
            dashCd: 0, dashTimer: 0, facing: 1, invuln: 0
        }},
        platforms: [
            {{ x: 0, y: 660, w: 1280, h: 60, type: 'solid', color: '#0f172a' }},
            {{ x: 180, y: 530, w: 180, h: 20, type: 'solid', color: '#1e293b' }},
            {{ x: 440, y: 440, w: 160, h: 20, type: 'moving_h', color: '#0ea5e9', minX: 420, maxX: 640, dir: 1, spd: 2.0 }},
            {{ x: 680, y: 360, w: 160, h: 20, type: 'crumble', color: '#f59e0b', shake: 0, alive: true, dropTimer: 0 }},
            {{ x: 920, y: 280, w: 160, h: 20, type: 'moving_v', color: '#8b5cf6', minY: 200, maxY: 380, dir: 1, spd: 1.8 }},
            {{ x: 1120, y: 220, w: 140, h: 20, type: 'solid', color: '#10b981' }}
        ],
        hazards: [
            {{ x: 380, y: 640, w: 50, h: 20, type: 'spikes' }},
            {{ x: 640, y: 640, w: 60, h: 20, type: 'spikes' }},
            {{ x: 860, y: 640, w: 50, h: 20, type: 'spikes' }}
        ],
        sawblades: [
            {{ x: 270, y: 490, r: 20, angle: 0, dir: 1, minX: 200, maxX: 340, spd: 2.5 }}
        ],
        crystals: [
            {{ x: 270, y: 470, r: 12, collected: false }},
            {{ x: 520, y: 390, r: 12, collected: false }},
            {{ x: 760, y: 310, r: 12, collected: false }},
            {{ x: 1000, y: 230, r: 12, collected: false }},
            {{ x: 1190, y: 170, r: 14, collected: false, isCore: true }}
        ],
        particles: [],
        floaters: [],
        skyStars: [],
        score: 0,
        hp: {hp_override if hp_override > 0 else 3},
        maxHp: {hp_override if hp_override > 0 else 3},
        gameOver: false,
        won: false
    }};

    for (let i = 0; i < 70; i++) {{
        state.skyStars.push({{
            x: Math.random() * 1280, y: Math.random() * 500,
            size: Math.random() * 2 + 1, alpha: Math.random() * 0.7 + 0.3
        }});
    }}

    function addFloater(x, y, text, color='#38bdf8') {{
        state.floaters.push({{ x, y, text, color, life: 1.0 }});
    }}

    function playerJump() {{
        const p = state.player;
        if (p.onWall !== 0 && !p.onGround) {{
            p.vy = -14.0 * {jump_mult:.1f};
            p.vx = -p.onWall * 8.5;
            p.onWall = 0;
            p.jumpsLeft = 1;
            audio.jump();
            screenShake(3);
            addFloater(p.x, p.y - 10, 'WALL JUMP!', '#38bdf8');
            return;
        }}
        if (p.jumpsLeft > 0) {{
            const isDouble = (p.jumpsLeft === 1);
            p.vy = -14.5 * {jump_mult:.1f};
            p.jumpsLeft--;
            p.onGround = false;
            audio.jump();
            if (isDouble) {{
                screenShake(3);
                addFloater(p.x, p.y - 10, 'DOUBLE JUMP!', '#a855f7');
                for (let i = 0; i < 14; i++) {{
                    state.particles.push({{
                        x: p.x + p.w/2, y: p.y + p.h,
                        vx: (Math.random() - 0.5) * 6, vy: Math.random() * 4 + 2,
                        life: 0.5, color: '#a855f7', size: 3
                    }});
                }}
            }}
        }}
    }}

    function playerDash() {{
        const p = state.player;
        if (p.dashCd > 0) return;
        p.dashCd = 45;
        p.dashTimer = 10;
        p.invuln = 15;
        p.vx = p.facing * 16.0;
        p.vy = 0;
        audio.dash();
        screenShake(4);
        addFloater(p.x, p.y - 10, 'CYBER DASH!', '#00f0ff');
    }}

    function resetGame() {{
        state.player.x = 80;
        state.player.y = 550;
        state.player.vx = 0;
        state.player.vy = 0;
        state.player.onGround = false;
        state.player.jumpsLeft = 2;
        state.player.dashCd = 0;
        state.player.dashTimer = 0;
        state.player.invuln = 0;
        state.crystals.forEach(c => c.collected = false);
        state.platforms.forEach(pl => {{
            if (pl.type === 'crumble') {{ pl.alive = true; pl.dropTimer = 0; pl.y = 360; }}
        }});
        state.score = 0;
        state.hp = state.maxHp;
        state.gameOver = false;
        state.won = false;
        if (scoreDisplay) scoreDisplay.textContent = 'Score: 0';
        if (hpDisplay) hpDisplay.textContent = 'HP: ' + state.maxHp;
    }}

    function update() {{
        if (state.gameOver || state.won) return;

        const p = state.player;
        if (p.dashCd > 0) p.dashCd--;
        if (p.invuln > 0) p.invuln--;

        const baseSpeed = 6.8 * {speed_mult:.1f};
        if (p.dashTimer > 0) {{
            p.dashTimer--;
            state.particles.push({{
                x: p.x, y: p.y, vx: 0, vy: 0, life: 0.3,
                color: '#00f0ff', size: 4
            }});
        }} else {{
            if (keys['ArrowLeft'] || keys['KeyA']) {{ p.vx = -baseSpeed; p.facing = -1; }}
            else if (keys['ArrowRight'] || keys['KeyD']) {{ p.vx = baseSpeed; p.facing = 1; }}
            else {{ p.vx *= 0.65; if (Math.abs(p.vx) < 0.2) p.vx = 0; }}

            p.vy += 0.8;
            if (p.vy > 18) p.vy = 18;
        }}

        p.x += p.vx;
        p.y += p.vy;

        p.onGround = false;
        p.onWall = 0;

        state.platforms.forEach(plat => {{
            if (!plat.alive && plat.type === 'crumble') return;

            if (plat.type === 'moving_h') {{
                plat.x += plat.spd * plat.dir;
                if (plat.x < plat.minX || plat.x > plat.maxX) plat.dir *= -1;
            }}
            if (plat.type === 'moving_v') {{
                plat.y += plat.spd * plat.dir;
                if (plat.y < plat.minY || plat.y > plat.maxY) plat.dir *= -1;
            }}
            if (plat.type === 'crumble' && plat.dropTimer > 0) {{
                plat.dropTimer--;
                plat.x += (Math.random() - 0.5) * 4;
                if (plat.dropTimer <= 0) {{
                    plat.alive = false;
                    audio.hit();
                    screenShake(5);
                }}
            }}

            if (p.x < plat.x + plat.w && p.x + p.w > plat.x &&
                p.y < plat.y + plat.h && p.y + p.h > plat.y) {{
                if (p.vy > 0 && p.y + p.h - p.vy <= plat.y + 12) {{
                    p.y = plat.y - p.h;
                    p.vy = 0;
                    p.onGround = true;
                    p.jumpsLeft = 2;
                    if (plat.type === 'crumble' && plat.dropTimer === 0) {{
                        plat.dropTimer = 35;
                    }}
                    if (plat.type === 'moving_h') p.x += plat.spd * plat.dir;
                }} else if (p.vx > 0 && p.x + p.w - p.vx <= plat.x + 8) {{
                    p.x = plat.x - p.w;
                    p.onWall = -1;
                }} else if (p.vx < 0 && p.x - p.vx >= plat.x + plat.w - 8) {{
                    p.x = plat.x + plat.w;
                    p.onWall = 1;
                }} else if (p.vy < 0) {{
                    p.y = plat.y + plat.h;
                    p.vy = 0;
                }}
            }}
        }});

        if (p.onWall !== 0 && !p.onGround && p.vy > 2.0) {{
            p.vy = 2.0;
            if (Math.random() < 0.4) {{
                state.particles.push({{
                    x: p.onWall === -1 ? p.x + p.w : p.x,
                    y: p.y + p.h/2,
                    vx: p.onWall * Math.random() * 3, vy: -Math.random() * 2,
                    life: 0.4, color: '#38bdf8', size: 2.5
                }});
            }}
        }}

        state.sawblades.forEach(sb => {{
            sb.x += sb.spd * sb.dir;
            if (sb.x < sb.minX || sb.x > sb.maxX) sb.dir *= -1;
            sb.angle += 0.2;

            if (p.invuln <= 0 && Math.hypot(p.x + p.w/2 - sb.x, p.y + p.h/2 - sb.y) < sb.r + 14) {{
                state.hp--;
                p.invuln = 40;
                audio.hit();
                screenShake(14);
                addFloater(p.x, p.y - 12, 'SAW DAMAGE -1 HP', '#ef4444');
                p.vy = -10;
                p.vx = -p.facing * 8;
                if (hpDisplay) hpDisplay.textContent = 'HP: ' + state.hp;
                if (state.hp <= 0) state.gameOver = true;
            }}
        }});

        state.hazards.forEach(hz => {{
            if (p.invuln <= 0 && p.x < hz.x + hz.w && p.x + p.w > hz.x && p.y + p.h > hz.y) {{
                state.hp--;
                p.invuln = 40;
                audio.hit();
                screenShake(14);
                addFloater(p.x, p.y - 12, 'SPIKE HAZARD -1 HP', '#ef4444');
                p.vy = -11;
                if (hpDisplay) hpDisplay.textContent = 'HP: ' + state.hp;
                if (state.hp <= 0) state.gameOver = true;
            }}
        }});

        let allCores = true;
        state.crystals.forEach(c => {{
            if (!c.collected) {{
                if (c.isCore) allCores = false;
                if (Math.hypot(p.x + p.w/2 - c.x, p.y + p.h/2 - c.y) < c.r + 22) {{
                    c.collected = true;
                    audio.pickup();
                    state.score += c.isCore ? 1000 : 250;
                    addFloater(c.x, c.y, c.isCore ? 'DATA CORE RETRIEVED!' : '+250', '#fbbf24');
                    if (scoreDisplay) scoreDisplay.textContent = 'Score: ' + state.score;
                    for (let i = 0; i < 14; i++) {{
                        state.particles.push({{
                            x: c.x, y: c.y,
                            vx: (Math.random()-0.5)*6, vy: (Math.random()-0.5)*6,
                            life: 0.6, color: '#38bdf8', size: 3
                        }});
                    }}
                }}
            }}
        }});

        if (allCores && !state.won) {{
            state.won = true;
            audio.win();
        }}

        if (p.y > 720) {{
            state.hp = 0;
            state.gameOver = true;
            audio.hit();
        }}

        for (let i = state.particles.length - 1; i >= 0; i--) {{
            const pt = state.particles[i];
            pt.x += pt.vx;
            pt.y += pt.vy;
            pt.life -= 0.025;
            if (pt.life <= 0) state.particles.splice(i, 1);
        }}

        for (let i = state.floaters.length - 1; i >= 0; i--) {{
            const f = state.floaters[i];
            f.y -= 1.0;
            f.life -= 0.025;
            if (f.life <= 0) state.floaters.splice(i, 1);
        }}
    }}

    function render() {{
        ctx.fillStyle = '#060814';
        ctx.fillRect(0, 0, canvas.width, canvas.height);

        ctx.save();
        if (shake > 0) {{
            ctx.translate((Math.random() - 0.5) * shake, (Math.random() - 0.5) * shake);
            shake *= 0.88;
            if (shake < 0.4) shake = 0;
        }}

        state.skyStars.forEach(s => {{
            ctx.fillStyle = '#38bdf8';
            ctx.globalAlpha = s.alpha;
            ctx.fillRect(s.x, s.y, s.size, s.size);
        }});
        ctx.globalAlpha = 1.0;

        ctx.fillStyle = '#0a1024';
        ctx.fillRect(60, 280, 140, 440);
        ctx.fillRect(260, 210, 180, 510);
        ctx.fillRect(520, 290, 160, 430);
        ctx.fillRect(740, 180, 200, 540);
        ctx.fillRect(1000, 260, 180, 460);

        ctx.fillStyle = '#1e293b';
        for (let bx = 80; bx < 1180; bx += 220) {{
            for (let by = 300; by < 640; by += 40) {{
                if ((bx + by) % 3 === 0) {{
                    ctx.fillStyle = '#38bdf822';
                    ctx.fillRect(bx, by, 16, 20);
                }}
            }}
        }}

        state.platforms.forEach(plat => {{
            if (!plat.alive && plat.type === 'crumble') return;
            ctx.fillStyle = plat.color;
            ctx.fillRect(plat.x, plat.y, plat.w, plat.h);

            ctx.strokeStyle = '#38bdf8';
            ctx.lineWidth = 2;
            ctx.beginPath();
            ctx.moveTo(plat.x, plat.y);
            ctx.lineTo(plat.x + plat.w, plat.y);
            ctx.stroke();
        }});

        ctx.fillStyle = '#ef4444';
        state.hazards.forEach(hz => {{
            const numSpikes = Math.floor(hz.w / 12);
            for (let i = 0; i < numSpikes; i++) {{
                ctx.beginPath();
                ctx.moveTo(hz.x + i * 12, hz.y + hz.h);
                ctx.lineTo(hz.x + i * 12 + 6, hz.y);
                ctx.lineTo(hz.x + i * 12 + 12, hz.y + hz.h);
                ctx.closePath();
                ctx.fill();
            }}
        }});

        state.sawblades.forEach(sb => {{
            ctx.save();
            ctx.translate(sb.x, sb.y);
            ctx.rotate(sb.angle);
            ctx.fillStyle = '#f59e0b';
            ctx.beginPath();
            ctx.arc(0, 0, sb.r, 0, Math.PI*2);
            ctx.fill();
            ctx.fillStyle = '#ef4444';
            for (let i = 0; i < 6; i++) {{
                ctx.rotate(Math.PI / 3);
                ctx.fillRect(-sb.r - 4, -3, 8, 6);
            }}
            ctx.restore();
        }});

        state.crystals.forEach(c => {{
            if (!c.collected) {{
                ctx.fillStyle = c.isCore ? '#a855f7' : '#00f0ff';
                ctx.shadowColor = c.isCore ? '#a855f7' : '#00f0ff';
                ctx.shadowBlur = 12;
                ctx.beginPath();
                ctx.moveTo(c.x, c.y - c.r);
                ctx.lineTo(c.x + c.r, c.y);
                ctx.lineTo(c.x, c.y + c.r);
                ctx.lineTo(c.x - c.r, c.y);
                ctx.closePath();
                ctx.fill();
                ctx.shadowBlur = 0;
            }}
        }});

        const p = state.player;
        if (p.invuln % 4 < 2) {{
            ctx.fillStyle = '#38bdf8';
            ctx.fillRect(p.x, p.y, p.w, p.h);

            ctx.fillStyle = '#ffffff';
            ctx.fillRect(p.facing === 1 ? p.x + 14 : p.x + 4, p.y + 10, 14, 6);

            ctx.fillStyle = '#f43f5e';
            ctx.fillRect(p.facing === 1 ? p.x - 8 : p.x + p.w, p.y + 12, 10, 5);
        }}

        state.particles.forEach(pt => {{
            ctx.fillStyle = pt.color;
            ctx.globalAlpha = Math.max(0, pt.life);
            ctx.fillRect(pt.x, pt.y, pt.size, pt.size);
        }});
        ctx.globalAlpha = 1.0;

        state.floaters.forEach(f => {{
            ctx.fillStyle = f.color;
            ctx.globalAlpha = Math.max(0, f.life);
            ctx.font = 'bold 14px monospace';
            ctx.textAlign = 'center';
            ctx.fillText(f.text, f.x, f.y);
        }});
        ctx.globalAlpha = 1.0;

        ctx.fillStyle = '#94a3b8';
        ctx.font = '12px monospace';
        ctx.textAlign = 'left';
        ctx.fillText('[A/D] Run  |  [Space] Jump (x2)  |  [Shift] Cyber Dash  |  Wall Slide & Jump', 50, 40);

        if (state.gameOver || state.won) {{
            ctx.fillStyle = 'rgba(0,0,0,0.85)';
            ctx.fillRect(0, 0, canvas.width, canvas.height);
            ctx.textAlign = 'center';
            if (state.won) {{
                ctx.fillStyle = '#10b981';
                ctx.font = 'bold 52px monospace';
                ctx.fillText('SECTOR CLEARED!', canvas.width / 2, canvas.height / 2 - 30);
                ctx.fillStyle = '#fff';
                ctx.font = '22px monospace';
                ctx.fillText('All Data Cores Collected! Final Score: ' + state.score, canvas.width / 2, canvas.height / 2 + 25);
            }} else {{
                ctx.fillStyle = '#ef4444';
                ctx.font = 'bold 52px monospace';
                ctx.fillText('SIGNAL LOST', canvas.width / 2, canvas.height / 2 - 30);
                ctx.fillStyle = '#94a3b8';
                ctx.font = '22px monospace';
                ctx.fillText('Score: ' + state.score + ' | Press [R] to Respawn', canvas.width / 2, canvas.height / 2 + 25);
            }}
        }}

        ctx.restore();
    }}
"""

    return f"""// Autonomous Dynamic Engine Canvas 2D
// Title: {gdd.title}
// Genre: {genre.upper()}

(function() {{{{
    const canvas = document.getElementById('gameCanvas');
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    const scoreDisplay = document.getElementById('scoreDisplay');
    const hpDisplay = document.getElementById('hpDisplay');

    {audio_code}

    {game_logic}

    function gameLoop() {{{{
        update();
        render();
        requestAnimationFrame(gameLoop);
    }}}}

    requestAnimationFrame(gameLoop);
}}}})();
"""


class WebAdapter(BaseEngineAdapter):
    """Web export adapter generating HTML5 Canvas 2D and Phaser-compatible playable games."""

    @property
    def target(self) -> EngineTarget:
        return EngineTarget.WEB_HTML5

    @property
    def display_name(self) -> str:
        return "HTML5 / Phaser Canvas Web Arcade"

    @property
    def file_extensions(self) -> List[str]:
        return [".html", ".js", ".css"]

    def get_supported_archetypes(self) -> List[str]:
        return ["platformer_2d", "top_down_rpg", "space_shooter", "grid_puzzle"]

    def validate_environment(self) -> Dict[str, Any]:
        return {
            "node_installed": bool(shutil.which("node")),
            "npx_installed": bool(shutil.which("npx")),
            "html5_supported": True,
        }

    def export_project(
        self,
        gdd: GameDesignDocument,
        manifest: AssetManifest,
        scenes: Union[List[SceneAST], Dict[str, SceneAST], List[Any], Dict[str, Any]],
        scripts: Union[List[ScriptSpec], Dict[str, ScriptSpec], List[Any], Dict[str, Any]],
        output_dir: Union[str, Path],
        **kwargs: Any,
    ) -> EngineProjectManifest:
        out_path = Path(output_dir).resolve()
        out_path.mkdir(parents=True, exist_ok=True)
        files_created: List[str] = []

        # 1. Structure
        for d in ("assets/sprites", "audio/sfx", "audio/music", "docs"):
            (out_path / d).mkdir(parents=True, exist_ok=True)

        # 2. Assets & Documentation
        asset_files = self.write_asset_files(manifest, out_path) if manifest else []
        files_created.extend(asset_files)
        doc_files = self.write_documentation(gdd, out_path) if gdd else []
        files_created.extend(doc_files)

        genre = (gdd.genre or "platformer_2d").lower()
        meta = getattr(gdd, "custom_metadata", {}) or {}
        hp_ovr = meta.get("hp_override")

        if "space" in genre or "shooter" in genre:
            controls_hint = "Controls: [Arrows / WASD] Move &bull; [Space] Rapid Plasma &bull; [B] Nova Bomb &bull; [R] Restart"
            genre_badge = "SPACE SHOOTER"
            max_hp = hp_ovr if hp_ovr else 3
            primary_color = "#38bdf8"
            bg_color = "#050714"
        elif "rpg" in genre or "dungeon" in genre:
            controls_hint = "Controls: [Arrows / WASD] Walk &bull; [Space] Sword Slash &bull; [F] Cast Fireball &bull; [Shift] Dash &bull; [R] Restart"
            genre_badge = "DUNGEON CRAWLER RPG"
            max_hp = hp_ovr if hp_ovr else 4
            primary_color = "#10b981"
            bg_color = "#0b1220"
        elif "puzzle" in genre:
            controls_hint = "Controls: [Mouse Click] Select & Swap Adjacent Gems &bull; [R] Reset Board"
            genre_badge = "MATCH PUZZLE"
            moves_count = int(20 * meta.get("speed_multiplier", 1.0))
            max_hp = f"{moves_count} Moves"
            primary_color = "#8b5cf6"
            bg_color = "#0e0f1e"
        else:
            controls_hint = "Controls: [Arrows / A-D] Move &bull; [Space / W] Jump &bull; [Shift] Dash &bull; [F] Shuriken &bull; [R] Restart"
            genre_badge = "PLATFORMER 2D"
            max_hp = hp_ovr if hp_ovr else 3
            primary_color = "#38bdf8"
            bg_color = "#0b0d17"

        # 3. style.css
        css_content = STYLE_CSS_TEMPLATE.format(
            bg_color=bg_color,
            primary_color=primary_color,
        )
        (out_path / "style.css").write_text(css_content, encoding="utf-8")
        files_created.append("style.css")

        # 4. game.js - Generated dynamically based on the exact GDD!
        js_code = _generate_dynamic_game_js(gdd)
        (out_path / "game.js").write_text(js_code, encoding="utf-8")
        files_created.append("game.js")

        # 5. index.html - Self-contained with both standalone links AND inline fallback for zero-dependency viewing
        html_content = INDEX_HTML_TEMPLATE.format(
            title=gdd.title,
            genre_badge=genre_badge,
            max_hp=max_hp,
            controls_hint=controls_hint,
            inline_css=css_content,
            inline_js=js_code,
        )
        (out_path / "index.html").write_text(html_content, encoding="utf-8")
        files_created.append("index.html")

        # 6. README.md
        readme = (
            f"# {gdd.title} (HTML5 Web Edition)\n\n"
            f"**Genre**: {genre_badge}\n\n"
            f"**Concept**: {gdd.high_concept}\n\n"
            "## Running Locally\n"
            "Open `index.html` directly in any web browser, or launch a local server:\n"
            "```bash\n"
            "python3 -m http.server 8080\n"
            "# Visit http://localhost:8080\n"
            "```\n"
        )
        (out_path / "README.md").write_text(readme, encoding="utf-8")
        files_created.append("README.md")

        return EngineProjectManifest(
            target=self.target,
            project_name=gdd.title,
            output_dir=str(out_path),
            entry_file="index.html",
            files_created=files_created,
            assets_linked=len(asset_files),
            scenes_created=1,
            scripts_created=1,
            readme_path="README.md",
            run_command=self.get_run_command(out_path),
            metadata={"web_engine": "Canvas2D / Web Audio"},
        )

    def get_run_command(self, project_dir: Union[str, Path]) -> List[str]:
        return ["python3", "-m", "http.server", "8000"]
