/**
 * studio/gui/static/agent_graph.js - 60fps Dynamic Agent Workflow Graph (F-GUI-02)
 * Vanilla HTML5 Canvas 2D engine with animated bezier particle flows, dynamic state rings,
 * Departure Mono typography, and zero external CDN dependencies.
 */

class AgentGraph {
    constructor(canvasId) {
        this.canvas = document.getElementById(canvasId);
        if (!this.canvas) return;
        this.ctx = this.canvas.getContext('2d');

        // Agent Topology Definition
        this.nodes = {
            'orchestrator': {
                id: 'orchestrator',
                name: 'Studio Orchestrator',
                role: 'Workflow Coordinator',
                x: 0.50, y: 0.18,
                color: '#a855f7',
                state: 'idle',
                task: 'Standing by',
                icon: 'ORCH',
                tokenCount: 0,
                costUsd: 0.0
            },
            'openai_director': {
                id: 'openai_director',
                name: 'OpenAI Director',
                role: 'Narrative & GDD',
                x: 0.22, y: 0.50,
                color: '#ffd000',
                state: 'idle',
                task: 'Waiting for prompt',
                icon: 'SPEC',
                tokenCount: 0,
                costUsd: 0.0
            },
            'gemini_artist': {
                id: 'gemini_artist',
                name: 'Gemini Artist',
                role: 'Multimodal Media',
                x: 0.50, y: 0.82,
                color: '#ff007f',
                state: 'idle',
                task: 'Asset generator ready',
                icon: 'ASSET',
                tokenCount: 0,
                costUsd: 0.0
            },
            'claude_engineer': {
                id: 'claude_engineer',
                name: 'Claude Engineer',
                role: 'Systems Architecture',
                x: 0.78, y: 0.50,
                color: '#00f0ff',
                state: 'idle',
                task: 'Code compiler idle',
                icon: 'CODE',
                tokenCount: 0,
                costUsd: 0.0
            },
            'validator': {
                id: 'validator',
                name: 'Automated Validator',
                role: 'Syntax & Simulation',
                x: 0.82, y: 0.82,
                color: '#00ff9d',
                state: 'idle',
                task: 'Verification ready',
                icon: 'QA',
                tokenCount: 0,
                costUsd: 0.0
            }
        };

        // Workflow Edges
        this.edges = [
            { from: 'orchestrator', to: 'openai_director', active: false, color: '#ffd000', label: 'Prompt / Directing' },
            { from: 'openai_director', to: 'gemini_artist', active: false, color: '#ff007f', label: 'Asset Requirements' },
            { from: 'openai_director', to: 'claude_engineer', active: false, color: '#00f0ff', label: 'Game Mechanics' },
            { from: 'gemini_artist', to: 'claude_engineer', active: false, color: '#ff007f', label: 'Manifest & Paths' },
            { from: 'claude_engineer', to: 'validator', active: false, color: '#00f0ff', label: 'Scenes & Scripts' },
            { from: 'gemini_artist', to: 'validator', active: false, color: '#ff007f', label: 'Binary Assets' },
            { from: 'validator', to: 'claude_engineer', active: false, color: '#ff3366', isReview: true, label: 'Review Feedback' },
            { from: 'validator', to: 'orchestrator', active: false, color: '#00ff9d', label: 'Report Pass/Fail' }
        ];

        this.particles = [];
        this.maxParticles = 120;
        this.hoveredNode = null;
        this.selectedNode = null;
        this.animTime = 0;
        this.width = 0;
        this.height = 0;

        this.init();
    }

    init() {
        this.resize();
        window.addEventListener('resize', () => this.resize());
        this.canvas.addEventListener('mousemove', (e) => this.handleMouseMove(e));
        this.canvas.addEventListener('click', (e) => this.handleClick(e));
        this.loop();
    }

    resize() {
        if (!this.canvas || !this.canvas.parentElement) return;
        const rect = this.canvas.parentElement.getBoundingClientRect();
        const dpr = window.devicePixelRatio || 1;
        this.canvas.width = Math.max(rect.width * dpr, 300);
        this.canvas.height = Math.max(rect.height * dpr, 200);
        this.canvas.style.width = `${rect.width}px`;
        this.canvas.style.height = `${rect.height}px`;
        this.ctx.setTransform(1, 0, 0, 1, 0, 0);
        this.ctx.scale(dpr, dpr);
        this.width = rect.width;
        this.height = rect.height;
    }

    setAgentState(agentId, state, taskInfo = '') {
        const node = this.nodes[agentId];
        if (node) {
            node.state = state;
            if (taskInfo) node.task = taskInfo;

            // Activate pipeline edges leading to and from this agent
            if (agentId === 'openai_director') {
                const e0 = this.edges.find(e => e.from === 'orchestrator' && e.to === 'openai_director');
                if (e0) e0.active = true;
                if (state === 'active') this.spawnBurst('orchestrator');
            } else if (agentId === 'gemini_artist') {
                const e1 = this.edges.find(e => e.from === 'openai_director' && e.to === 'gemini_artist');
                if (e1) e1.active = true;
                if (state === 'active') this.spawnBurst('openai_director');
            } else if (agentId === 'claude_engineer') {
                const e2 = this.edges.find(e => e.from === 'gemini_artist' && e.to === 'claude_engineer');
                const e3 = this.edges.find(e => e.from === 'openai_director' && e.to === 'claude_engineer');
                if (e2) e2.active = true;
                if (e3) e3.active = true;
                if (state === 'active') {
                    this.spawnBurst('gemini_artist');
                    this.spawnBurst('openai_director');
                }
            } else if (agentId === 'validator') {
                const e4 = this.edges.find(e => e.from === 'claude_engineer' && e.to === 'validator');
                if (e4) e4.active = true;
                if (state === 'active') this.spawnBurst('claude_engineer');
            } else if (agentId === 'orchestrator' && state === 'complete') {
                const e5 = this.edges.find(e => e.from === 'validator' && e.to === 'orchestrator');
                if (e5) e5.active = true;
                this.spawnBurst('validator');
            }

            // Also keep outbound edges alive when actively processing
            this.edges.forEach(edge => {
                if (edge.from === agentId && (state === 'active' || state === 'reviewing')) {
                    edge.active = true;
                }
            });

            if (state === 'active') {
                this.spawnBurst(agentId);
            }
        }
    }

    resetAll() {
        Object.values(this.nodes).forEach(n => {
            n.state = 'idle';
            n.task = 'Standing by';
        });
        this.edges.forEach(e => {
            e.active = false;
        });
        this.particles = [];
    }

    spawnBurst(sourceAgentId) {
        const relevantEdges = this.edges.filter(e => e.from === sourceAgentId);
        relevantEdges.forEach(edge => {
            for (let i = 0; i < 12; i++) {
                if (this.particles.length >= this.maxParticles) break;
                this.particles.push({
                    from: edge.from,
                    to: edge.to,
                    t: Math.random() * 0.2,
                    speed: 0.010 + Math.random() * 0.008,
                    size: 3.0 + Math.random() * 1.5,
                    color: edge.color,
                    trail: []
                });
            }
        });
    }

    handleMouseMove(e) {
        const rect = this.canvas.getBoundingClientRect();
        const mx = e.clientX - rect.left;
        const my = e.clientY - rect.top;

        this.hoveredNode = null;
        for (const node of Object.values(this.nodes)) {
            const nx = node.x * this.width;
            const ny = node.y * this.height;
            const dist = Math.hypot(mx - nx, my - ny);
            if (dist <= 36) {
                this.hoveredNode = node;
                this.canvas.style.cursor = 'pointer';
                return;
            }
        }
        this.canvas.style.cursor = 'default';
    }

    handleClick(e) {
        if (this.hoveredNode) {
            this.selectedNode = this.hoveredNode;
            if (window.studioApp && typeof window.studioApp.onNodeSelected === 'function') {
                window.studioApp.onNodeSelected(this.hoveredNode);
            }
        }
    }

    loop() {
        this.animTime += 0.02;
        this.update();
        this.render();
        requestAnimationFrame(() => this.loop());
    }

    update() {
        // Update particles along bezier curves
        for (let i = this.particles.length - 1; i >= 0; i--) {
            const p = this.particles[i];
            p.t += p.speed;
            if (p.t >= 1) {
                this.particles.splice(i, 1);
            }
        }

        // Ambient particles on active edges
        this.edges.forEach(edge => {
            if (edge.active && Math.random() < 0.18 && this.particles.length < this.maxParticles) {
                this.particles.push({
                    from: edge.from,
                    to: edge.to,
                    t: 0,
                    speed: 0.006 + Math.random() * 0.005,
                    size: 2.2,
                    color: edge.color,
                    trail: []
                });
            }
        });
    }

    render() {
        this.ctx.clearRect(0, 0, this.width, this.height);
        this.renderGrid();
        this.renderEdges();
        this.renderParticles();
        this.renderNodes();
        if (this.hoveredNode) {
            this.renderTooltip(this.hoveredNode);
        }
    }

    renderGrid() {
        this.ctx.save();
        this.ctx.strokeStyle = '#121829';
        this.ctx.lineWidth = 0.5;
        const step = 28;
        for (let x = 0; x < this.width; x += step) {
            this.ctx.beginPath();
            this.ctx.moveTo(x, 0);
            this.ctx.lineTo(x, this.height);
            this.ctx.stroke();
        }
        for (let y = 0; y < this.height; y += step) {
            this.ctx.beginPath();
            this.ctx.moveTo(0, y);
            this.ctx.lineTo(this.width, y);
            this.ctx.stroke();
        }
        this.ctx.restore();
    }

    getBezierPoints(fromNode, toNode) {
        const x1 = fromNode.x * this.width;
        const y1 = fromNode.y * this.height;
        const x2 = toNode.x * this.width;
        const y2 = toNode.y * this.height;

        // Quadratic control point with slight curve
        const mx = (x1 + x2) / 2;
        const my = (y1 + y2) / 2;
        const dx = x2 - x1;
        const dy = y2 - y1;
        const cx = mx - dy * 0.15;
        const cy = my + dx * 0.15;

        return { x1, y1, cx, cy, x2, y2 };
    }

    renderEdges() {
        this.edges.forEach(edge => {
            const fromNode = this.nodes[edge.from];
            const toNode = this.nodes[edge.to];
            if (!fromNode || !toNode) return;

            const { x1, y1, cx, cy, x2, y2 } = this.getBezierPoints(fromNode, toNode);

            this.ctx.save();
            this.ctx.beginPath();
            this.ctx.moveTo(x1, y1);
            this.ctx.quadraticCurveTo(cx, cy, x2, y2);

            if (edge.active) {
                this.ctx.strokeStyle = edge.color;
                this.ctx.lineWidth = 2.4;
                this.ctx.shadowColor = edge.color;
                this.ctx.shadowBlur = 10;
                this.ctx.setLineDash([8, 6]);
                this.ctx.lineDashOffset = -this.animTime * 35;
            } else if (edge.isReview) {
                this.ctx.strokeStyle = 'rgba(255, 51, 102, 0.25)';
                this.ctx.lineWidth = 1.0;
                this.ctx.setLineDash([4, 4]);
            } else {
                this.ctx.strokeStyle = '#1e2942';
                this.ctx.lineWidth = 1.2;
            }

            this.ctx.stroke();
            this.ctx.restore();
        });
    }

    renderParticles() {
        this.ctx.save();
        this.particles.forEach(p => {
            const fromNode = this.nodes[p.from];
            const toNode = this.nodes[p.to];
            if (!fromNode || !toNode) return;

            const { x1, y1, cx, cy, x2, y2 } = this.getBezierPoints(fromNode, toNode);
            const t = p.t;

            // Quadratic Bezier formulation
            const px = (1 - t) * (1 - t) * x1 + 2 * (1 - t) * t * cx + t * t * x2;
            const py = (1 - t) * (1 - t) * y1 + 2 * (1 - t) * t * cy + t * t * y2;

            // Render glowing particle
            this.ctx.beginPath();
            this.ctx.arc(px, py, p.size, 0, Math.PI * 2);
            this.ctx.fillStyle = p.color;
            this.ctx.shadowColor = p.color;
            this.ctx.shadowBlur = 6;
            this.ctx.fill();
        });
        this.ctx.restore();
    }

    renderNodes() {
        Object.values(this.nodes).forEach(node => {
            const nx = node.x * this.width;
            const ny = node.y * this.height;
            const radius = 32;

            this.ctx.save();

            // Active or Reviewing Halo Animation
            if (node.state === 'active') {
                const pulse = (Math.sin(this.animTime * 4) + 1) * 5;
                this.ctx.beginPath();
                this.ctx.arc(nx, ny, radius + 4 + pulse, 0, Math.PI * 2);
                this.ctx.strokeStyle = node.color;
                this.ctx.lineWidth = 2;
                this.ctx.globalAlpha = 0.5;
                this.ctx.stroke();
                this.ctx.globalAlpha = 1.0;
            } else if (node.state === 'reviewing') {
                this.ctx.beginPath();
                this.ctx.arc(nx, ny, radius + 5, 0, Math.PI * 2);
                this.ctx.strokeStyle = '#ffd000';
                this.ctx.setLineDash([4, 4]);
                this.ctx.lineWidth = 2;
                this.ctx.stroke();
            }

            // Node Base Fill
            this.ctx.beginPath();
            this.ctx.arc(nx, ny, radius, 0, Math.PI * 2);
            this.ctx.fillStyle = node.state === 'active' ? '#141c30' : '#0c101c';
            this.ctx.fill();

            // Node Border
            this.ctx.lineWidth = 2.2;
            this.ctx.strokeStyle = node.state === 'idle' ? '#27344f' : node.color;
            this.ctx.stroke();

            // Professional Vector Technical Node Glyph
            this.renderNodeGlyph(node, nx, ny - 6);

            // Agent Name with Departure Mono font
            this.ctx.font = '10px "Departure Mono", "JetBrains Mono", monospace';
            this.ctx.fillStyle = '#f8fafc';
            this.ctx.fillText(node.name, nx, ny + 18);

            // State Badge Pill
            this.renderBadge(nx, ny + 32, node.state, node.color);

            // Live Agent Task Info Text
            if (node.task && node.task !== 'Standing by') {
                this.ctx.font = '9px "JetBrains Mono", monospace';
                this.ctx.fillStyle = node.state === 'active' ? '#38bdf8' : '#94a3b8';
                const taskText = node.task.length > 28 ? node.task.substring(0, 26) + '...' : node.task;
                this.ctx.fillText(taskText, nx, ny + 46);
            }

            this.ctx.restore();
        });
    }

    renderBadge(x, y, state, accentColor) {
        this.ctx.save();
        this.ctx.font = '9px "Departure Mono", "JetBrains Mono", monospace';
        const txt = state.toUpperCase();
        const metrics = this.ctx.measureText(txt);
        const w = metrics.width + 10;
        const h = 14;

        this.ctx.fillStyle = state === 'complete' ? 'rgba(0, 255, 157, 0.2)' :
                             state === 'active' ? `${accentColor}33` :
                             state === 'reviewing' ? 'rgba(255, 208, 0, 0.25)' :
                             state === 'failed' ? 'rgba(255, 51, 102, 0.25)' : 'rgba(39, 52, 79, 0.35)';

        this.ctx.strokeStyle = state === 'complete' ? '#00ff9d' :
                              state === 'active' ? accentColor :
                              state === 'reviewing' ? '#ffd000' :
                              state === 'failed' ? '#ff3366' : '#4b5563';
        this.ctx.lineWidth = 1;

        // Rounded badge
        if (typeof this.ctx.roundRect === 'function') {
            this.ctx.beginPath();
            this.ctx.roundRect(x - w / 2, y - h / 2, w, h, 3);
            this.ctx.fill();
            this.ctx.stroke();
        } else {
            this.ctx.fillRect(x - w / 2, y - h / 2, w, h);
            this.ctx.strokeRect(x - w / 2, y - h / 2, w, h);
        }

        this.ctx.fillStyle = '#ffffff';
        this.ctx.textAlign = 'center';
        this.ctx.textBaseline = 'middle';
        this.ctx.fillText(txt, x, y);
        this.ctx.restore();
    }

    renderNodeGlyph(node, cx, cy) {
        this.ctx.save();
        const strokeColor = node.state === 'active' ? '#ffffff' : (node.state === 'complete' ? '#00ff9d' : node.color);
        this.ctx.strokeStyle = strokeColor;
        this.ctx.fillStyle = strokeColor;
        this.ctx.lineWidth = 1.8;
        this.ctx.lineCap = 'round';
        this.ctx.lineJoin = 'round';

        if (node.id === 'orchestrator') {
            // Central Diamond Core
            this.ctx.beginPath();
            this.ctx.moveTo(cx, cy - 8);
            this.ctx.lineTo(cx + 8, cy);
            this.ctx.lineTo(cx, cy + 8);
            this.ctx.lineTo(cx - 8, cy);
            this.ctx.closePath();
            this.ctx.stroke();
            this.ctx.beginPath();
            this.ctx.arc(cx, cy, 2.5, 0, Math.PI * 2);
            this.ctx.fill();
        } else if (node.id === 'openai_director') {
            // Spec / GDD Document Glyph
            this.ctx.beginPath();
            this.ctx.rect(cx - 7, cy - 8, 14, 16);
            this.ctx.stroke();
            this.ctx.beginPath();
            this.ctx.moveTo(cx - 4, cy - 3);
            this.ctx.lineTo(cx + 4, cy - 3);
            this.ctx.moveTo(cx - 4, cy + 1);
            this.ctx.lineTo(cx + 4, cy + 1);
            this.ctx.moveTo(cx - 4, cy + 5);
            this.ctx.lineTo(cx + 2, cy + 5);
            this.ctx.stroke();
        } else if (node.id === 'gemini_artist') {
            // Media / Palette Geometric Glyph
            this.ctx.beginPath();
            this.ctx.arc(cx, cy, 8, 0, Math.PI * 2);
            this.ctx.stroke();
            this.ctx.beginPath();
            this.ctx.arc(cx - 3, cy - 3, 1.8, 0, Math.PI * 2);
            this.ctx.arc(cx + 3, cy - 3, 1.8, 0, Math.PI * 2);
            this.ctx.arc(cx, cy + 3, 1.8, 0, Math.PI * 2);
            this.ctx.fill();
        } else if (node.id === 'claude_engineer') {
            // Systems / Code Brackets Glyph < / >
            this.ctx.beginPath();
            this.ctx.moveTo(cx - 4, cy - 5);
            this.ctx.lineTo(cx - 8, cy);
            this.ctx.lineTo(cx - 4, cy + 5);
            this.ctx.moveTo(cx + 4, cy - 5);
            this.ctx.lineTo(cx + 8, cy);
            this.ctx.lineTo(cx + 4, cy + 5);
            this.ctx.moveTo(cx + 2, cy - 7);
            this.ctx.lineTo(cx - 2, cy + 7);
            this.ctx.stroke();
        } else if (node.id === 'validator') {
            // Shield & Checkmark Glyph
            this.ctx.beginPath();
            this.ctx.moveTo(cx, cy - 8);
            this.ctx.lineTo(cx + 7, cy - 4);
            this.ctx.lineTo(cx + 7, cy + 2);
            this.ctx.quadraticCurveTo(cx + 7, cy + 8, cx, cy + 10);
            this.ctx.quadraticCurveTo(cx - 7, cy + 8, cx - 7, cy + 2);
            this.ctx.lineTo(cx - 7, cy - 4);
            this.ctx.closePath();
            this.ctx.stroke();
            this.ctx.beginPath();
            this.ctx.moveTo(cx - 3, cy);
            this.ctx.lineTo(cx - 1, cy + 3);
            this.ctx.lineTo(cx + 4, cy - 2);
            this.ctx.stroke();
        } else {
            this.ctx.font = 'bold 10px monospace';
            this.ctx.textAlign = 'center';
            this.ctx.textBaseline = 'middle';
            this.ctx.fillText(node.icon || 'NODE', cx, cy);
        }

        this.ctx.restore();
    }

    renderTooltip(node) {
        const nx = node.x * this.width;
        const ny = node.y * this.height;

        this.ctx.save();
        this.ctx.font = '10px "Departure Mono", "JetBrains Mono", monospace';
        const lines = [
            `AGENT: ${node.name}`,
            `ROLE: ${node.role}`,
            `STATE: ${node.state.toUpperCase()}`,
            `TASK: ${node.task}`
        ];

        let maxW = 0;
        lines.forEach(l => {
            const w = this.ctx.measureText(l).width;
            if (w > maxW) maxW = w;
        });

        const boxW = maxW + 20;
        const boxH = lines.length * 15 + 12;
        let bx = nx - boxW / 2;
        let by = ny - 45 - boxH;

        if (bx < 10) bx = 10;
        if (bx + boxW > this.width - 10) bx = this.width - boxW - 10;
        if (by < 10) by = ny + 45;

        this.ctx.fillStyle = 'rgba(10, 14, 24, 0.94)';
        this.ctx.strokeStyle = node.color;
        this.ctx.lineWidth = 1;

        if (typeof this.ctx.roundRect === 'function') {
            this.ctx.beginPath();
            this.ctx.roundRect(bx, by, boxW, boxH, 4);
            this.ctx.fill();
            this.ctx.stroke();
        } else {
            this.ctx.fillRect(bx, by, boxW, boxH);
            this.ctx.strokeRect(bx, by, boxW, boxH);
        }

        this.ctx.fillStyle = '#f8fafc';
        this.ctx.textAlign = 'left';
        this.ctx.textBaseline = 'top';

        lines.forEach((line, i) => {
            if (i === 0) this.ctx.fillStyle = node.color;
            else if (i === 2) this.ctx.fillStyle = '#00ff9d';
            else this.ctx.fillStyle = '#cbd5e1';
            this.ctx.fillText(line, bx + 10, by + 8 + i * 15);
        });

        this.ctx.restore();
    }
}

// Global initialization helper
window.AgentGraph = AgentGraph;
