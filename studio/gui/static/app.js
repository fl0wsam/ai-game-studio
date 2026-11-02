/**
 * studio/gui/static/app.js - AI Game Studio UI State Manager & Controller
 * Integrates WebSockets, REST APIs, Audio Player dock, Media Gallery, and Inspector tabs.
 */

class StudioApp {
    constructor() {
        this.graph = null;
        this.ws = null;
        this.projectState = null;
        this.currentFilter = 'all';
        this.assets = [];
        this.autoScrollLogs = true;
        this.activeAudio = null;
        this.eqInterval = null;
        this.projectNameEdited = false;

        document.addEventListener('DOMContentLoaded', () => this.init());
    }

    init() {
        // Initialize Agent Graph
        this.graph = new AgentGraph('agentGraphCanvas');
        window.studioApp = this;

        this.setupDirectingControls();
        this.setupDirectorWorkflow();
        this.setupTabs();
        this.setupGalleryFilters();
        this.setupTerminalControls();
        this.setupAudioPlayer();
        this.setupModal();

        this.connectWebSocket();
        this.fetchProjectState();
    }

    // ------------------------------------------------------------------------
    // WebSocket Telemetry Connection
    // ------------------------------------------------------------------------

    connectWebSocket() {
        const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        const wsUrl = `${protocol}//${window.location.host}/api/ws/telemetry`;
        const dot = document.getElementById('wsConnectionDot');
        const text = document.getElementById('wsConnectionText');

        if (dot) dot.className = 'hud-dot busy';
        if (text) text.textContent = 'CONNECTING';

        try {
            this.ws = new WebSocket(wsUrl);

            this.ws.onopen = () => {
                if (dot) dot.className = 'hud-dot online';
                if (text) text.textContent = 'ONLINE';
                this.log('SYSTEM', 'INFO', 'Connected to real-time studio telemetry stream.');
            };

            this.ws.onmessage = (event) => {
                try {
                    const data = JSON.parse(event.data);
                    this.handleTelemetryEvent(data);
                } catch (e) {
                    console.debug('Received non-JSON socket message:', event.data);
                }
            };

            this.ws.onclose = () => {
                if (dot) dot.className = 'hud-dot';
                if (text) text.textContent = 'DISCONNECTED';
                setTimeout(() => this.connectWebSocket(), 3000);
            };

            this.ws.onerror = () => {
                if (this.ws) this.ws.close();
            };
        } catch (err) {
            console.error('WebSocket connection error:', err);
            setTimeout(() => this.connectWebSocket(), 4000);
        }
    }

    handleTelemetryEvent(event) {
        if (!event || !event.event_type) return;

        // 1. Agent State Transitions
        if (event.event_type === 'agent_state' && event.agent) {
            if (this.graph) {
                this.graph.setAgentState(event.agent, event.state || 'idle', event.data?.task || event.data?.action || '');
            }
        }

        // 2. Log Stream
        if (event.event_type === 'log_chunk') {
            const agent = event.agent || 'STUDIO';
            const level = event.data?.level || 'INFO';
            const msg = event.data?.message || JSON.stringify(event.data);
            this.log(agent, level, msg);
        }

        // 3. Asset Generated
        if (event.event_type === 'asset_generated' && event.data) {
            this.addAsset(event.data);
            this.log('GEMINI', 'SUCCESS', `Asset created: ${event.data.rel_path || event.data.asset_id}`);
        }

        // 4. Verification Outcome
        if (event.event_type === 'verification_done' && event.data) {
            this.updateDiagnostics(event.data);
            const passed = !!event.data.passed;
            this.log('VALIDATOR', passed ? 'SUCCESS' : 'ERROR', `Verification finished: ${passed ? 'PASSED (0 errors)' : 'FAILED'}`);
        }

        // 5. Token Metrics
        if (event.event_type === 'token_metrics' && event.data) {
            this.updateTokenMetrics(event.data);
        }

        // 6. Pipeline Lifecycle Markers
        if (event.event_type === 'pipeline_started') {
            this.setHUDStatus('IN_PROGRESS', event.data?.phase || 'INITIALIZING');
            this.log('ORCHESTRATOR', 'INFO', `Pipeline launched for project: ${event.data?.project_name || 'Game'}`);

            // Wipe stale assets & workspace views on new build
            this.assets = [];
            this.renderGallery();
            this.updateGalleryCounts();

            const tree = document.getElementById('projectFileTree');
            if (tree) tree.innerHTML = '<div style="color:var(--text-muted);font-size:11px;padding:8px;">Generating fresh project hierarchy...</div>';
            const scenes = document.getElementById('sceneHierarchyList');
            if (scenes) scenes.innerHTML = '<div style="color:var(--text-muted);font-size:11px;padding:8px;">Generating scenes...</div>';
            const scripts = document.getElementById('scriptList');
            if (scripts) scripts.innerHTML = '<div style="color:var(--text-muted);font-size:11px;padding:8px;">Generating scripts...</div>';

            if (event.data?.genre) {
                const gb = document.getElementById('badgeDetectedGenre');
                if (gb) {
                    gb.innerHTML = `<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="4" y="4" width="16" height="16" rx="2"></rect><rect x="9" y="9" width="6" height="6"></rect><line x1="9" y1="1" x2="9" y2="4"></line><line x1="15" y1="1" x2="15" y2="4"></line><line x1="9" y1="20" x2="9" y2="23"></line><line x1="15" y1="20" x2="15" y2="23"></line><line x1="20" y1="9" x2="23" y2="9"></line><line x1="20" y1="14" x2="23" y2="14"></line><line x1="1" y1="9" x2="4" y2="9"></line><line x1="1" y1="14" x2="4" y2="14"></line></svg> <span>AI Genre: ${this.escapeHtml(event.data.genre.toUpperCase())}</span>`;
                }
            }

            if (this.graph && typeof this.graph.resetAll === 'function') {
                this.graph.resetAll();
            }
        } else if (event.event_type === 'pipeline_completed') {
            this.setHUDStatus('COMPLETED', 'FINISHED');
            this.log('ORCHESTRATOR', 'SUCCESS', 'Pipeline completed successfully!');
            this.fetchProjectState();
        } else if (event.event_type === 'pipeline_failed') {
            this.setHUDStatus('FAILED', 'ERROR');
            this.log('ORCHESTRATOR', 'ERROR', `Pipeline failed: ${event.data?.error || 'Unknown error'}`);
        }
    }

    // ------------------------------------------------------------------------
    // REST API Integration
    // ------------------------------------------------------------------------

    async fetchProjectState() {
        try {
            const res = await fetch('/api/project/state');
            if (res.ok) {
                this.projectState = await res.json();
                this.renderState(this.projectState);
            }
        } catch (err) {
            console.error('Failed to load project state:', err);
        }
    }

    renderState(state) {
        if (!state) return;

        // HUD update
        this.setHUDStatus(state.status, state.active_phase);
        const projectName = state.project_name;
        const projectInput = document.getElementById('inputProjectName');
        if (projectInput && projectName && !this.projectNameEdited) projectInput.value = projectName;

        // Assets populate
        if (state.asset_manifest && Array.isArray(state.asset_manifest.items)) {
            this.assets = state.asset_manifest.items;
            this.renderGallery();
        }

        // GDD populate
        if (state.gdd) {
            this.renderGDD(state.gdd);
        }

        // Scenes populate
        if (state.scenes) {
            this.renderScenes(state.scenes);
        }

        // Scripts populate
        if (state.scripts) {
            this.renderScripts(state.scripts);
        }

        // Project Explorer Tree update
        this.renderProjectTree(state);

        // Verification populate
        if (state.verification_report) {
            this.updateDiagnostics(state.verification_report);
        }

        // Token metrics populate
        if (state.token_metrics) {
            this.updateTokenMetrics(state.token_metrics);
        }
    }

    // ------------------------------------------------------------------------
    // Directing Panel Controls
    // ------------------------------------------------------------------------

    setupDirectingControls() {
        const launchBtn = document.getElementById('btnLaunch');
        const exportBtn = document.getElementById('btnExport');
        const verifyBtn = document.getElementById('btnVerify');
        const playBtn = document.getElementById('btnPlayGame');
        const promptInput = document.getElementById('inputPrompt');
        const projectNameInput = document.getElementById('inputProjectName');

        if (playBtn) {
            playBtn.addEventListener('click', (e) => {
                e.preventDefault();
                window.open('/game?t=' + Date.now(), '_blank');
            });
        }

        if (launchBtn) {
            launchBtn.addEventListener('click', async () => {
                const prompt = (promptInput?.value || '').trim();
                if (!prompt) {
                    this.notify('Add a game idea before building.', 'warning');
                    return;
                }
                const engine = document.getElementById('selectEngine')?.value || 'godot4';
                const mock = document.getElementById('checkMock')?.checked ?? true;
                const projectName = (projectNameInput?.value || '').trim() || 'My First Game';

                // Clear stale assets and state immediately in GUI
                this.assets = [];
                this.renderGallery();
                this.updateGalleryCounts();

                const tree = document.getElementById('projectFileTree');
                if (tree) tree.innerHTML = '<div style="color:var(--text-muted);font-size:11px;padding:8px;">Starting build...</div>';
                const badge = document.getElementById('badgeDetectedGenre');
                if (badge) {
                    badge.innerHTML = `<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="4" y="4" width="16" height="16" rx="2"></rect><rect x="9" y="9" width="6" height="6"></rect><line x1="9" y1="1" x2="9" y2="4"></line><line x1="15" y1="1" x2="15" y2="4"></line><line x1="9" y1="20" x2="9" y2="23"></line><line x1="15" y1="20" x2="15" y2="23"></line><line x1="20" y1="9" x2="23" y2="9"></line><line x1="20" y1="14" x2="23" y2="14"></line><line x1="1" y1="9" x2="4" y2="9"></line><line x1="1" y1="14" x2="4" y2="14"></line></svg> <span>AI Genre: Analyzing prompt...</span>`;
                }

                try {
                    launchBtn.disabled = true;
                    launchBtn.textContent = 'BUILDING GAME...';

                    const res = await fetch('/api/direct', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({
                            prompt: prompt,
                            target_engine: engine,
                            genre: 'auto',
                            mock: mock,
                            project_name: projectName
                        })
                    });

                    if (res.ok) {
                        const data = await res.json();
                        this.log('STUDIO', 'INFO', `Generation started: ${data.project_name} [Genre: ${data.genre}]`);
                        if (badge && data.genre) {
                            badge.innerHTML = `<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="4" y="4" width="16" height="16" rx="2"></rect><rect x="9" y="9" width="6" height="6"></rect><line x1="9" y1="1" x2="9" y2="4"></line><line x1="15" y1="1" x2="15" y2="4"></line><line x1="9" y1="20" x2="9" y2="23"></line><line x1="15" y1="20" x2="15" y2="23"></line><line x1="20" y1="9" x2="23" y2="9"></line><line x1="20" y1="14" x2="23" y2="14"></line><line x1="1" y1="9" x2="4" y2="9"></line><line x1="1" y1="14" x2="4" y2="14"></line></svg> <span>AI Genre: ${this.escapeHtml(data.genre.toUpperCase())}</span>`;
                        }
                    } else {
                        const err = await res.json();
                        this.notify(`Launch error: ${err.detail || 'Failed to start'}`, 'error');
                    }
                } catch (e) {
                    this.notify(`Network error: ${e.message}`, 'error');
                } finally {
                    launchBtn.disabled = false;
                    launchBtn.textContent = '▶ Build My Game';
                }
            });
        }

        // Iterative Refinement & Post-Build Prompting
        const refineBtn = document.getElementById('btnRefine');
        const refineInput = document.getElementById('inputRefine');
        if (refineBtn) {
            refineBtn.addEventListener('click', async () => {
                const prompt = (refineInput?.value || '').trim();
                if (!prompt) {
                    this.notify('Describe one change for the team to make.', 'warning');
                    return;
                }

                try {
                    refineBtn.disabled = true;
                    refineBtn.textContent = 'REFINING...';
                    this.log('STUDIO', 'INFO', `Applying iterative refinement: "${prompt}"`);

                    const res = await fetch('/api/project/refine', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ prompt: prompt })
                    });

                    if (res.ok) {
                        const data = await res.json();
                        this.log('CLAUDE', 'SUCCESS', `Refinement applied: ${data.message} (v${data.version})`);
                        await this.fetchProjectState();
                        this.notify('Change applied. Open Play Game to try it.', 'success');
                    } else {
                        const err = await res.json();
                        this.notify(`Refinement error: ${err.detail || 'Failed to refine'}`, 'error');
                    }
                } catch (e) {
                    this.notify(`Network error: ${e.message}`, 'error');
                } finally {
                    refineBtn.disabled = false;
                    refineBtn.innerHTML = '<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"></polyline></svg> <span>Refine Game</span>';
                }
            });
        }

        if (exportBtn) {
            exportBtn.addEventListener('click', async () => {
                const targetDir = prompt(
                    'Enter destination path to export game project:',
                    './exported_game'
                );
                if (!targetDir || !targetDir.trim()) {
                    return; // user cancelled
                }

                try {
                    exportBtn.disabled = true;
                    exportBtn.textContent = 'EXPORTING...';
                    const res = await fetch('/api/project/export', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ output_dir: targetDir.trim(), overwrite: true })
                    });
                    if (res.ok) {
                        const data = await res.json();
                        this.notify(`Project exported: ${data.total_files} files`, 'success');
                        this.log('PACKAGER', 'SUCCESS', `Project permanently exported to ${data.output_dir} (${data.total_files} files)`);
                    } else {
                        const err = await res.json();
                        this.notify(`Export error: ${err.detail || 'Failed'}`, 'error');
                    }
                } catch (e) {
                    this.notify(`Export error: ${e.message}`, 'error');
                } finally {
                    exportBtn.disabled = false;
                    exportBtn.innerHTML = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"></path><polyline points="7 10 12 15 17 10"></polyline><line x1="12" y1="15" x2="12" y2="3"></line></svg> <span>Export Game</span>';
                }
            });
        }

        if (verifyBtn) {
            verifyBtn.addEventListener('click', async () => {
                try {
                    verifyBtn.disabled = true;
                    verifyBtn.textContent = 'TESTING...';
                    const res = await fetch('/api/project/verify', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ fail_fast: false })
                    });
                    if (res.ok) {
                        const data = await res.json();
                        this.updateDiagnostics(data);
                        this.log('VALIDATOR', data.passed ? 'SUCCESS' : 'ERROR', `Verification: ${data.passed ? 'PASSED' : 'FAILED'}`);
                    }
                } catch (e) {
                    this.notify(`Verification error: ${e.message}`, 'error');
                } finally {
                    verifyBtn.disabled = false;
                    verifyBtn.innerHTML = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"></path></svg> <span>Verify Project</span>';
                }
            });
        }

        // Preset Chips
        document.querySelectorAll('.chip').forEach(chip => {
            chip.addEventListener('click', () => {
                const preset = chip.getAttribute('data-preset');
                if (preset && promptInput) {
                    promptInput.value = preset;
                    const presetProjectName = chip.getAttribute('data-project-name');
                    const projectNameInput = document.getElementById('inputProjectName');
                    if (presetProjectName && projectNameInput) {
                        projectNameInput.value = presetProjectName;
                        this.projectNameEdited = true;
                    }
                    const engineSelect = document.getElementById('selectEngine');
                    const genreBadge = document.getElementById('badgeDetectedGenre');
                    const presetGenre = chip.getAttribute('data-genre');
                    if (chip.getAttribute('data-engine') && engineSelect) engineSelect.value = chip.getAttribute('data-engine');
                    if (presetGenre && genreBadge) {
                        genreBadge.innerHTML = `<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="4" y="4" width="16" height="16" rx="2"></rect><rect x="9" y="9" width="6" height="6"></rect><line x1="9" y1="1" x2="9" y2="4"></line><line x1="15" y1="1" x2="15" y2="4"></line><line x1="9" y1="20" x2="9" y2="23"></line><line x1="15" y1="20" x2="15" y2="23"></line><line x1="20" y1="9" x2="23" y2="9"></line><line x1="20" y1="14" x2="23" y2="14"></line><line x1="1" y1="9" x2="4" y2="9"></line><line x1="1" y1="14" x2="4" y2="14"></line></svg> <span>AI Genre: ${presetGenre.toUpperCase()}</span>`;
                    }
                }
            });
        });
    }

    setupDirectorWorkflow() {
        const projectInput = document.getElementById('inputProjectName');
        if (projectInput) {
            projectInput.addEventListener('input', () => {
                this.projectNameEdited = true;
                projectInput.value = projectInput.value.replace(/\s+/g, ' ').slice(0, 60);
            });
        }
    }

    notify(message, type = 'info') {
        const region = document.getElementById('toastRegion');
        if (!region) return;
        const toast = document.createElement('div');
        toast.className = `toast toast-${type}`;
        toast.innerHTML = `<span class="toast-mark" aria-hidden="true"></span><span>${this.escapeHtml(message)}</span>`;
        region.appendChild(toast);
        requestAnimationFrame(() => toast.classList.add('visible'));
        window.setTimeout(() => {
            toast.classList.remove('visible');
            window.setTimeout(() => toast.remove(), 220);
        }, 3600);
    }

    // ------------------------------------------------------------------------
    // Tabs Navigation
    // ------------------------------------------------------------------------

    setupTabs() {
        const tabBtns = document.querySelectorAll('.tab-btn');
        tabBtns.forEach(btn => {
            btn.addEventListener('click', () => {
                const tabId = btn.getAttribute('data-tab');
                tabBtns.forEach(b => b.classList.remove('active'));
                document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));

                btn.classList.add('active');
                const target = document.getElementById(tabId);
                if (target) target.classList.add('active');
            });
        });
    }

    // ------------------------------------------------------------------------
    // Terminal Log Console
    // ------------------------------------------------------------------------

    setupTerminalControls() {
        const clearBtn = document.getElementById('btnClearLogs');
        const copyBtn = document.getElementById('btnCopyLogs');
        const filterSelect = document.getElementById('logFilterSelect');
        const autoScrollCheck = document.getElementById('checkAutoScroll');

        if (clearBtn) {
            clearBtn.addEventListener('click', () => {
                const stream = document.getElementById('terminalStream');
                if (stream) stream.innerHTML = '';
            });
        }

        if (copyBtn) {
            copyBtn.addEventListener('click', () => {
                const stream = document.getElementById('terminalStream');
                if (stream) {
                    navigator.clipboard.writeText(stream.innerText);
                    copyBtn.textContent = 'COPIED!';
                    setTimeout(() => copyBtn.textContent = 'COPY', 1500);
                }
            });
        }

        if (autoScrollCheck) {
            autoScrollCheck.addEventListener('change', () => {
                this.autoScrollLogs = autoScrollCheck.checked;
            });
        }

        if (filterSelect) {
            filterSelect.addEventListener('change', () => {
                const val = filterSelect.value.toUpperCase();
                document.querySelectorAll('.log-entry').forEach(entry => {
                    const level = entry.getAttribute('data-level') || '';
                    if (val === 'ALL' || level === val) {
                        entry.style.display = 'flex';
                    } else {
                        entry.style.display = 'none';
                    }
                });
            });
        }
    }

    log(agent, level, message) {
        const stream = document.getElementById('terminalStream');
        if (!stream) return;

        const entry = document.createElement('div');
        entry.className = 'log-entry';
        entry.setAttribute('data-level', level.toUpperCase());

        const d = new Date();
        const timeStr = d.toTimeString().split(' ')[0];

        entry.innerHTML = `
            <span class="log-time">[${timeStr}]</span>
            <span class="log-agent">${agent}</span>
            <span class="log-level log-level-${level.toUpperCase()}">[${level.toUpperCase()}]</span>
            <span class="log-msg">${this.escapeHtml(message)}</span>
        `;

        stream.appendChild(entry);

        if (this.autoScrollLogs) {
            stream.scrollTop = stream.scrollHeight;
        }
    }

    // ------------------------------------------------------------------------
    // Media & Asset Gallery
    // ------------------------------------------------------------------------

    setupGalleryFilters() {
        const filterPills = document.querySelectorAll('.filter-pill');
        filterPills.forEach(pill => {
            pill.addEventListener('click', () => {
                filterPills.forEach(p => p.classList.remove('active'));
                pill.classList.add('active');
                this.currentFilter = pill.getAttribute('data-filter') || 'all';
                this.renderGallery();
            });
        });

        const searchInput = document.getElementById('gallerySearch');
        if (searchInput) {
            searchInput.addEventListener('input', () => this.renderGallery());
        }
    }

    addAsset(assetData) {
        // Prevent duplicate items
        const exists = this.assets.find(a => a.asset_id === assetData.asset_id);
        if (!exists) {
            this.assets.unshift(assetData);
        } else {
            Object.assign(exists, assetData);
        }
        this.renderGallery();
        this.updateGalleryCounts();
    }

    renderGallery() {
        const grid = document.getElementById('galleryGrid');
        if (!grid) return;

        const searchTerm = (document.getElementById('gallerySearch')?.value || '').toLowerCase();
        grid.innerHTML = '';

        const filtered = this.assets.filter(item => {
            const typeStr = String(item.asset_type || '').toLowerCase();
            const relPath = String(item.rel_path || '').toLowerCase();
            const id = String(item.asset_id || '').toLowerCase();

            // Type filter
            if (this.currentFilter === 'sprites' && !typeStr.includes('sprite')) return false;
            if (this.currentFilter === 'textures' && !typeStr.includes('texture')) return false;
            if (this.currentFilter === 'sfx' && (!typeStr.includes('sfx') && !typeStr.includes('audio_sfx'))) return false;
            if (this.currentFilter === 'music' && (!typeStr.includes('music') && !typeStr.includes('bgm'))) return false;
            if (this.currentFilter === 'cutscenes' && !typeStr.includes('cutscene')) return false;

            // Search filter
            if (searchTerm && !relPath.includes(searchTerm) && !id.includes(searchTerm)) return false;

            return true;
        });

        if (filtered.length === 0) {
            grid.innerHTML = '<div style="color: var(--text-dim); padding: 24px; text-align: center; grid-column: 1/-1;">No assets found matching filter.</div>';
            return;
        }

        filtered.forEach(item => {
            const card = document.createElement('div');
            card.className = 'asset-card';

            const isAudio = String(item.asset_type).includes('audio') || String(item.rel_path).endsWith('.wav') || String(item.rel_path).endsWith('.mp3');
            const assetUrl = `/api/project/assets/${item.asset_id}?v=${encodeURIComponent(item.checksum || Date.now())}`;

            let previewHtml = '';
            if (isAudio) {
                previewHtml = `
                    <div class="asset-preview" style="background: #0d1222;">
                        <button class="btn btn-mini btn-play-audio" data-id="${item.asset_id}" data-url="${assetUrl}" data-name="${item.rel_path}" style="padding: 8px 14px; font-size: 13px;">▶ PLAY AUDIO</button>
                    </div>
                `;
            } else {
                previewHtml = `
                    <div class="asset-preview">
                        <img src="${assetUrl}" alt="${item.asset_id}" loading="lazy" onerror="this.style.display='none';">
                    </div>
                `;
            }

            card.innerHTML = `
                ${previewHtml}
                <div class="asset-meta">
                    <div class="asset-id" title="${item.asset_id}">${item.asset_id}</div>
                    <div class="asset-sub" title="${item.rel_path}">${item.rel_path}</div>
                    <div class="asset-actions">
                        ${!isAudio ? `<button class="btn-mini btn-view-img" data-url="${assetUrl}" data-id="${item.asset_id}">ZOOM</button>` : ''}
                        <a href="${assetUrl}" download="${item.asset_id}.bin" class="btn-mini" style="text-decoration: none; text-align: center;">DL</a>
                    </div>
                </div>
            `;

            grid.appendChild(card);
        });

        // Attach card actions
        grid.querySelectorAll('.btn-view-img').forEach(btn => {
            btn.addEventListener('click', () => {
                this.openImageModal(btn.getAttribute('data-url'), btn.getAttribute('data-id'));
            });
        });

        grid.querySelectorAll('.btn-play-audio').forEach(btn => {
            btn.addEventListener('click', () => {
                this.playAudioTrack(btn.getAttribute('data-url'), btn.getAttribute('data-name'));
            });
        });
    }

    updateGalleryCounts() {
        const total = this.assets.length;
        const countSpan = document.getElementById('countAllAssets');
        if (countSpan) countSpan.textContent = total;
    }

    // ------------------------------------------------------------------------
    // HTML5 Audio Player Dock
    // ------------------------------------------------------------------------

    setupAudioPlayer() {
        this.audioElement = new Audio();
        const playBtn = document.getElementById('btnAudioPlay');
        const scrubBar = document.getElementById('audioScrubBar');
        const timeDisplay = document.getElementById('audioTimeDisplay');

        if (playBtn) {
            playBtn.addEventListener('click', () => {
                if (!this.audioElement.src) return;
                if (this.audioElement.paused) {
                    this.audioElement.play();
                    playBtn.textContent = '⏸ PAUSE';
                    this.startEqualizer();
                } else {
                    this.audioElement.pause();
                    playBtn.textContent = '▶ PLAY';
                    this.stopEqualizer();
                }
            });
        }

        this.audioElement.addEventListener('timeupdate', () => {
            if (this.audioElement.duration && scrubBar) {
                scrubBar.value = (this.audioElement.currentTime / this.audioElement.duration) * 100;
            }
            if (timeDisplay) {
                const cur = this.formatTime(this.audioElement.currentTime);
                const dur = this.formatTime(this.audioElement.duration || 0);
                timeDisplay.textContent = `${cur} / ${dur}`;
            }
        });

        this.audioElement.addEventListener('ended', () => {
            if (playBtn) playBtn.textContent = '▶ PLAY';
            this.stopEqualizer();
        });

        if (scrubBar) {
            scrubBar.addEventListener('input', () => {
                if (this.audioElement.duration) {
                    this.audioElement.currentTime = (scrubBar.value / 100) * this.audioElement.duration;
                }
            });
        }
    }

    playAudioTrack(url, trackName) {
        if (!this.audioElement) return;
        this.audioElement.src = url;
        this.audioElement.play().catch(e => console.warn('Audio play prevented:', e));

        const titleEl = document.getElementById('audioTrackTitle');
        const subEl = document.getElementById('audioTrackSub');
        const playBtn = document.getElementById('btnAudioPlay');

        if (titleEl) titleEl.textContent = trackName || 'Audio Track';
        if (subEl) subEl.textContent = 'Playing...';
        if (playBtn) playBtn.textContent = '⏸ PAUSE';
        this.startEqualizer();
    }

    startEqualizer() {
        this.stopEqualizer();
        const bars = document.querySelectorAll('.eq-bar');
        this.eqInterval = setInterval(() => {
            bars.forEach(bar => {
                const h = Math.floor(Math.random() * 16) + 4;
                bar.style.height = `${h}px`;
            });
        }, 120);
    }

    stopEqualizer() {
        if (this.eqInterval) {
            clearInterval(this.eqInterval);
            this.eqInterval = null;
        }
        document.querySelectorAll('.eq-bar').forEach(bar => bar.style.height = '4px');
    }

    formatTime(seconds) {
        if (isNaN(seconds)) return '00:00';
        const mins = Math.floor(seconds / 60);
        const secs = Math.floor(seconds % 60);
        return `${mins.toString().padStart(2, '0')}:${secs.toString().padStart(2, '0')}`;
    }

    // ------------------------------------------------------------------------
    // Image Modal Lightbox
    // ------------------------------------------------------------------------

    setupModal() {
        const modal = document.getElementById('imageModal');
        const closeBtn = document.getElementById('btnModalClose');
        if (closeBtn && modal) {
            closeBtn.addEventListener('click', () => modal.classList.remove('open'));
            modal.addEventListener('click', (e) => {
                if (e.target === modal) modal.classList.remove('open');
            });
        }
    }

    openImageModal(url, title) {
        const modal = document.getElementById('imageModal');
        const img = document.getElementById('modalImage');
        const titleEl = document.getElementById('modalTitle');
        if (modal && img) {
            img.src = url;
            if (titleEl) titleEl.textContent = title || 'Artwork Preview';
            modal.classList.add('open');
        }
    }

    // ------------------------------------------------------------------------
    // Inspectors: GDD, Scenes, Scripts, Diagnostics
    // ------------------------------------------------------------------------

    renderGDD(gdd) {
        const container = document.getElementById('tabGddBody');
        if (!container) return;

        let mechanicsHtml = '';
        if (Array.isArray(gdd.gameplay_mechanics)) {
            mechanicsHtml = gdd.gameplay_mechanics.map(m => `<li>${this.escapeHtml(m)}</li>`).join('');
        }

        container.innerHTML = `
            <div class="gdd-section">
                <div class="gdd-section-title">${this.escapeHtml(gdd.title || 'Game Design Document')}</div>
                <p><strong>Genre:</strong> ${this.escapeHtml(gdd.genre || 'N/A')}</p>
                <p><strong>High Concept:</strong> ${this.escapeHtml(gdd.high_concept || 'N/A')}</p>
            </div>
            <div class="gdd-section">
                <div class="gdd-section-title">Core Gameplay Mechanics</div>
                <ul style="padding-left: 20px;">${mechanicsHtml || '<li>No mechanics listed</li>'}</ul>
            </div>
            <div class="gdd-section">
                <div class="gdd-section-title">Narrative & Lore</div>
                <p>${this.escapeHtml(gdd.narrative_lore || 'No narrative lore provided.')}</p>
            </div>
        `;
    }

    renderScenes(scenes) {
        const container = document.getElementById('tabScenesBody');
        if (!container) return;
        const sceneList = Object.values(scenes || {});
        if (sceneList.length === 0) {
            container.innerHTML = '<p style="color: var(--text-dim);">No scene trees generated yet.</p>';
            return;
        }

        container.innerHTML = sceneList.map(scn => `
            <div class="gdd-section">
                <div class="gdd-section-title">Scene: ${this.escapeHtml(scn.name)} (${this.escapeHtml(scn.path)})</div>
                <div class="code-viewer">${this.escapeHtml(JSON.stringify(scn, null, 2))}</div>
            </div>
        `).join('');
    }

    renderScripts(scripts) {
        const container = document.getElementById('tabScriptsBody');
        if (!container) return;
        const scriptList = Object.values(scripts || {});
        if (scriptList.length === 0) {
            container.innerHTML = '<p style="color: var(--text-dim);">No scripts compiled yet.</p>';
            return;
        }

        container.innerHTML = scriptList.map(sc => `
            <div class="gdd-section">
                <div class="gdd-section-title">Script: ${this.escapeHtml(sc.rel_path)} [${this.escapeHtml(sc.language)}]</div>
                <div class="code-viewer">${this.escapeHtml(sc.code || '# Empty script')}</div>
            </div>
        `).join('');
    }

    renderProjectTree(state) {
        const container = document.getElementById('dynamicFileNodes');
        const rootLabel = document.getElementById('treeRootLabel');
        if (!container) return;

        const projectName = state.project_name || 'Project';
        const engine = state.target_engine || 'godot4';
        if (rootLabel) {
            rootLabel.textContent = `res:// [${projectName}] (${engine})`;
        }

        const nodes = [];

        // Assets directory
        if (state.asset_manifest?.items && state.asset_manifest.items.length > 0) {
            nodes.push(`
                <div class="tree-node" style="padding-left: 18px; font-weight: bold; color: var(--color-gemini);">
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"></path></svg>
                    <span>assets/ (${state.asset_manifest.items.length} items)</span>
                </div>
            `);
            state.asset_manifest.items.slice(0, 5).forEach(item => {
                const isAudio = item.rel_path?.endsWith('.wav') || item.rel_path?.endsWith('.mp3');
                const tag = isAudio ? '<span style="color:#a78bfa;font-family:monospace;margin-right:4px;">[SFX]</span>' : '<span style="color:#ff007f;font-family:monospace;margin-right:4px;">[TEX]</span>';
                nodes.push(`
                    <div class="tree-node" style="padding-left: 32px; font-size: 10px; color: var(--text-secondary);" title="${this.escapeHtml(item.rel_path)}">
                        ${tag} ${this.escapeHtml(item.rel_path)}
                    </div>
                `);
            });
            if (state.asset_manifest.items.length > 5) {
                nodes.push(`
                    <div class="tree-node" style="padding-left: 32px; font-size: 10px; color: var(--text-muted);">
                        ... +${state.asset_manifest.items.length - 5} more assets
                    </div>
                `);
            }
        }

        // Scenes
        const scenes = Object.values(state.scenes || {});
        if (scenes.length > 0) {
            nodes.push(`
                <div class="tree-node" style="padding-left: 18px; font-weight: bold; color: var(--color-claude);">
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polygon points="12 2 2 7 12 12 22 7 12 2"></polygon></svg>
                    <span>scenes/ (${scenes.length} scenes)</span>
                </div>
            `);
            scenes.forEach(scn => {
                nodes.push(`
                    <div class="tree-node" style="padding-left: 32px; font-size: 10px; color: var(--text-main);" title="${this.escapeHtml(scn.path)}">
                        <span style="color:#38bdf8;font-family:monospace;margin-right:4px;">[SCN]</span> ${this.escapeHtml(scn.name)} (${this.escapeHtml(scn.path)})
                    </div>
                `);
            });
        }

        // Scripts
        const scripts = Object.values(state.scripts || {});
        if (scripts.length > 0) {
            nodes.push(`
                <div class="tree-node" style="padding-left: 18px; font-weight: bold; color: var(--color-claude);">
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="16 18 22 12 16 6"></polyline><polyline points="8 6 2 12 8 18"></polyline></svg>
                    <span>scripts/ (${scripts.length} scripts)</span>
                </div>
            `);
            scripts.forEach(sc => {
                nodes.push(`
                    <div class="tree-node" style="padding-left: 32px; font-size: 10px; color: var(--text-main);" title="${this.escapeHtml(sc.rel_path)}">
                        <span style="color:#00f0ff;font-family:monospace;margin-right:4px;">[SRC]</span> ${this.escapeHtml(sc.rel_path)} [${this.escapeHtml(sc.language)}]
                    </div>
                `);
            });
        }

        // Docs / GDD
        if (state.gdd) {
            nodes.push(`
                <div class="tree-node" style="padding-left: 18px; font-weight: bold; color: var(--color-openai);">
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"></path></svg>
                    <span>docs/</span>
                </div>
                <div class="tree-node" style="padding-left: 32px; font-size: 10px; color: var(--text-main);">
                    <span style="color:#ffd000;font-family:monospace;margin-right:4px;">[DOC]</span> GDD.md (${this.escapeHtml(state.gdd.genre || 'GDD')})
                </div>
            `);
        }

        if (nodes.length > 0) {
            container.innerHTML = nodes.join('');
        }
    }

    updateDiagnostics(report) {
        const container = document.getElementById('tabDiagBody');
        if (!container) return;

        const passed = !!report.passed;
        const errors = report.errors || [];
        const timeMs = report.execution_time_ms ? `${report.execution_time_ms.toFixed(1)}ms` : 'N/A';

        let rowsHtml = '';
        if (errors.length === 0) {
            rowsHtml = `<tr><td colspan="5" style="text-align: center; color: var(--neon-green);">Zero errors. All syntax, schema, and asset checks passed.</td></tr>`;
        } else {
            rowsHtml = errors.map(err => `
                <tr>
                    <td><span class="diag-badge fail">${err.severity || 'ERROR'}</span></td>
                    <td>${this.escapeHtml(err.stage || 'general')}</td>
                    <td>${this.escapeHtml(err.file_path || '-')}</td>
                    <td>${err.line || '-'}</td>
                    <td>${this.escapeHtml(err.message || '')}</td>
                </tr>
            `).join('');
        }

        container.innerHTML = `
            <div style="display: flex; gap: 10px; align-items: center; margin-bottom: 12px;">
                <span class="diag-badge ${passed ? 'pass' : 'fail'}" style="font-size: 13px; padding: 4px 10px;">
                    ${passed ? 'PASSED' : 'VERIFICATION FAILED'}
                </span>
                <span style="color: var(--text-dim); font-size: 11px;">Completed in ${timeMs} (${errors.length} errors reported)</span>
            </div>
            <table class="diag-table">
                <thead>
                    <tr>
                        <th>SEV</th>
                        <th>STAGE</th>
                        <th>FILE</th>
                        <th>LINE</th>
                        <th>MESSAGE</th>
                    </tr>
                </thead>
                <tbody>
                    ${rowsHtml}
                </tbody>
            </table>
        `;
    }

    updateTokenMetrics(metrics) {
        let totalTokens = 0;
        let totalCost = 0.0;

        if (metrics && typeof metrics === 'object') {
            Object.values(metrics).forEach(m => {
                totalTokens += (m.total_tokens || 0);
                totalCost += (m.estimated_cost_usd || 0.0);
            });
        }

        const costEl = document.getElementById('metricCostUsd');
        const tokEl = document.getElementById('metricTotalTokens');

        if (costEl) costEl.textContent = `$${totalCost.toFixed(4)}`;
        if (tokEl) tokEl.textContent = totalTokens.toLocaleString();
    }

    setHUDStatus(status, phase) {
        const statusEl = document.getElementById('hudProjectStatus');
        const phaseEl = document.getElementById('hudActivePhase');
        if (statusEl) statusEl.textContent = String(status || 'PENDING').toUpperCase();
        if (phaseEl) phaseEl.textContent = String(phase || 'INIT').toUpperCase();
        this.updateDirectorWorkflow(status, phase);
    }

    updateDirectorWorkflow(status, phase) {
        const statusText = String(status || '').toLowerCase();
        const phaseText = String(phase || '').toLowerCase();
        let currentStage = 'idea';
        let message = 'Ready for your first idea';

        if (statusText === 'in_progress' || statusText === 'running') {
            if (phaseText.includes('gdd') || phaseText.includes('design') || phaseText.includes('plan')) {
                currentStage = 'plan';
                message = 'Your designers are making the game plan';
            } else if (phaseText.includes('asset') || phaseText.includes('art') || phaseText.includes('audio') || phaseText.includes('scene') || phaseText.includes('script')) {
                currentStage = 'build';
                message = 'Your team is building the game';
            } else {
                currentStage = 'build';
                message = 'Your AI team is working';
            }
        } else if (statusText === 'completed' || statusText === 'complete' || statusText === 'success') {
            currentStage = 'play';
            message = 'Your demo is ready to play and improve';
        } else if (statusText === 'failed' || statusText === 'error') {
            currentStage = 'build';
            message = 'The team needs your help fixing a build problem';
        }

        const stages = ['idea', 'plan', 'build', 'play'];
        const currentIndex = stages.indexOf(currentStage);
        document.querySelectorAll('.director-stage').forEach(stage => {
            const stageIndex = stages.indexOf(stage.dataset.stage || 'idea');
            stage.classList.toggle('active', stageIndex === currentIndex);
            stage.classList.toggle('complete', stageIndex < currentIndex);
        });
        const progressText = document.getElementById('directorProgressText');
        if (progressText) progressText.textContent = message;
    }

    onNodeSelected(node) {
        this.log('USER', 'INFO', `Selected agent node: ${node.name} [${node.state.toUpperCase()}]`);
    }

    escapeHtml(str) {
        if (str === null || str === undefined) return '';
        return String(str)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#039;');
    }
}

// Instantiate Global Controller
const studioApp = new StudioApp();
