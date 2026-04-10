require 'json'

module GaussianPoints
  module UIparts
    module GaussianSplattingDialog
      @@dialog = nil

      def self.show_dialog
        if @@dialog && @@dialog.visible?
          @@dialog.bring_to_front
          push_state
          return
        end

        @@dialog = UI::HtmlDialog.new(
          dialog_title: 'Gaussian Splatting',
          width: 380,
          height: 400,
          scrollable: true,
          resizable: true,
          style: UI::HtmlDialog::STYLE_DIALOG
        )

        @@dialog.set_html(dialog_html)
        bind_callbacks
        @@dialog.center
        @@dialog.show
      end

      def self.dialog_html
        <<~HTML
          <!DOCTYPE html>
          <html lang="en">
          <head>
            <meta charset="UTF-8">
            <title>Gaussian Points Studio</title>
            <link rel="preconnect" href="https://fonts.googleapis.com">
            <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
            <link href="https://fonts.googleapis.com/css2?family=DM+Sans:opsz,wght@9..40,400;9..40,500&family=JetBrains+Mono:wght@400;500;700&family=Outfit:wght@300;400;500;600;700&display=swap" rel="stylesheet">
            <script src="https://cdn.tailwindcss.com"></script>
            <script>
              tailwind.config = {
                theme: {
                  extend: {
                    colors: {
                      accent: '#FF5400',
                      surface: '#0a0a0d',
                      surfaceElevated: '#111116',
                    }
                  }
                }
              }
            </script>
            <style>
              :root {
                --bg-base: #050505;
                --bg-surface: #0a0a0d;
                --bg-surface-elevated: #111116;
                --bg-surface-hover: #16161d;
                --border-color: rgba(255, 255, 255, 0.08);
                --border-light: rgba(255, 255, 255, 0.15);
                --accent-primary: #FF5400;
                --accent-cyan: #00F0FF;
                --accent-magenta: #FF2E93;
                --text-main: #FAFAFA;
                --text-muted: #A1A1AA;
              }
              body {
                font-family: 'Outfit', sans-serif;
                background-color: var(--bg-base);
                color: var(--text-main);
                margin: 0;
              }
              .font-mono { font-family: 'JetBrains Mono', monospace; }
              .font-body { font-family: 'DM Sans', sans-serif; }
              
              .glass-panel {
                background: rgba(16, 16, 22, 0.6);
                backdrop-filter: blur(24px);
                -webkit-backdrop-filter: blur(24px);
                border: 1px solid var(--border-color);
                box-shadow: inset 0 1px 1px rgba(255, 255, 255, 0.05), 0 8px 32px rgba(0, 0, 0, 0.5);
              }

              .custom-scrollbar::-webkit-scrollbar { width: 5px; }
              .custom-scrollbar::-webkit-scrollbar-track { background: transparent; }
              .custom-scrollbar::-webkit-scrollbar-thumb { background: rgba(255, 255, 255, 0.1); border-radius: 10px; }
              .custom-scrollbar::-webkit-scrollbar-thumb:hover { background: rgba(255, 255, 255, 0.25); }

              /* Input Range Styling Override */
              input[type=range] {
                -webkit-appearance: none;
                width: 100%;
                background: transparent;
                outline: none;
              }
              input[type=range]::-webkit-slider-thumb {
                -webkit-appearance: none;
                height: 12px; width: 12px;
                border-radius: 50%;
                background: var(--accent-primary);
                cursor: pointer;
                margin-top: -4px;
                box-shadow: 0 0 8px var(--accent-primary);
              }
              input[type=range]::-webkit-slider-runnable-track {
                width: 100%; height: 4px;
                cursor: pointer;
                background: var(--border-color);
                border-radius: 2px;
              }

              /* Custom toggle switch */
              .switch-checkbox:checked { right: 0; }
              .switch-checkbox:checked + .switch-label { background-color: var(--accent-primary); border-color: var(--accent-primary); }
              .switch-checkbox:checked + .switch-label .switch-dot { transform: translateX(12px); background-color: white;}
            </style>
          </head>
          <body class="w-full h-screen overflow-hidden flex flex-col relative select-none">
            
            <!-- Header (Compact) -->
            <header class="h-10 border-b border-white/10 bg-[#0A0A0D]/90 backdrop-blur-md px-4 flex items-center justify-between shrink-0 shadow-md">
              <div class="flex items-center gap-2">
                <div class="w-1.5 h-1.5 rounded-full bg-accent shadow-[0_0_8px_#FF5400] animate-pulse" id="statusDot"></div>
                <h1 class="text-xs font-semibold tracking-wide">Splat Studio</h1>
              </div>
              <div class="text-[9px] font-mono text-zinc-500 uppercase px-1.5 py-0.5 rounded bg-white/5 border border-white/10 truncate max-w-[200px]" id="statusBar">
                System Initializing...
              </div>
            </header>

            <div class="flex-1 overflow-y-auto overflow-x-hidden custom-scrollbar p-3 relative flex flex-col gap-3">
              
              <!-- Engine Status (Inline) -->
              <section class="glass-panel rounded-2xl px-3 p-2.5 flex items-center justify-between gap-3 text-[10px]">
                <div class="flex items-center gap-2 overflow-hidden flex-1">
                  <div class="flex items-center gap-1.5 shrink-0">
                    <span class="text-zinc-500 font-bold uppercase tracking-wide">Bridge</span>
                    <span class="font-mono text-zinc-300" id="dllState">...</span>
                  </div>
                  <div class="w-px h-3 bg-white/10 shrink-0"></div>
                  <div class="flex items-center gap-1.5 shrink-0">
                    <span class="text-zinc-500 font-bold uppercase tracking-wide">Render</span>
                    <span class="font-mono text-zinc-300" id="initState">...</span>
                  </div>
                  <div class="w-px h-3 bg-white/10 shrink-0"></div>
                  <div class="font-mono text-zinc-600 truncate flex-1" id="sandboxPath" title="Workspace path">
                    ...
                  </div>
                </div>
              </section>

              <!-- Dataset & Action (Merged) -->
              <section class="glass-panel rounded-2xl p-3 relative overflow-hidden">
                <div class="absolute top-0 right-0 w-20 h-20 bg-accent/10 blur-[20px] rounded-full pointer-events-none transform translate-x-5 -translate-y-5"></div>
                
                <h3 class="text-[9px] font-bold text-zinc-500 uppercase tracking-widest mb-2 flex items-center gap-2 relative z-10">
                  Dataset Operations <span class="flex-1 h-px bg-white/5"></span>
                </h3>
                
                <div class="flex items-center gap-2 mb-2 relative z-10 w-full">
                  <button class="flex-1 py-2 rounded-full bg-gradient-to-r from-accent to-[#FF2E93] hover:opacity-90 text-white shadow-[0_0_10px_rgba(255,84,0,0.2)] text-xs font-bold transition-all flex items-center justify-center gap-1.5" data-action="load_scene">
                    <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-8l-4-4m0 0L8 8m4-4v12"></path></svg>
                    Load GASP/PLY
                  </button>
                  <button class="w-[70px] py-2 rounded-full bg-white/5 hover:bg-white/10 text-zinc-300 text-[10px] font-semibold transition-all flex items-center justify-center border border-white/10" title="Check PLY without loading" data-action="analyze_ply">
                    Analyze
                  </button>
                  <button class="w-[70px] py-2 rounded-full bg-rose-500/10 hover:bg-rose-500/20 text-rose-400 text-[10px] font-semibold transition-all flex items-center justify-center border border-rose-500/20" title="Clear All Plats" data-action="clear_splats">
                    Clear
                  </button>
                </div>

                <div class="flex items-center justify-between relative z-10 mt-3 pt-3 border-t border-white/5">
                  <label class="text-[9px] font-bold text-zinc-500 uppercase tracking-widest shrink-0">Import Axis Mode</label>
                  <select id="upAxisMode" class="bg-black/40 border border-white/10 text-zinc-300 text-[11px] rounded-full px-2 py-1 outline-none focus:border-accent cursor-pointer max-w-[160px]">
                    <option value="legacy">Y-up (Legacy/Postshot)</option>
                    <option value="swap_a">Z-up Inverted</option>
                    <option value="swap_b" selected>Z-up (Default/Luma)</option>
                  </select>
                </div>
              </section>

              <!-- Parameters -->
              <section class="glass-panel rounded-2xl p-3 relative overflow-hidden">
                <h3 class="text-[9px] font-bold text-zinc-500 uppercase tracking-widest mb-2 flex items-center gap-2 relative z-10">
                  Visuals & Processing <span class="flex-1 h-px bg-white/5"></span>
                </h3>
                
                <div class="grid grid-cols-2 gap-2 relative z-10">
                  <div class="bg-black/30 border border-white/5 p-2 rounded-xl flex flex-col justify-center">
                    <div class="flex justify-between items-center mb-1">
                      <span class="text-[10px] font-semibold text-zinc-300">SH Harmonic Level</span>
                      <span class="font-mono text-accent font-bold text-[10px]" id="shDegreeValue">3</span>
                    </div>
                    <input id="shDegree" type="range" min="0" max="3" step="1" value="3" class="w-full h-1" />
                  </div>

                  <div class="bg-black/30 border border-white/5 p-2 rounded-xl flex items-center justify-between overflow-hidden">
                    <div class="overflow-hidden">
                      <div class="text-[10px] font-semibold text-zinc-300 truncate">Approximate Sort</div>
                      <div class="text-[8px] text-zinc-600 leading-none mt-1 truncate">Boost FPS / Flicker</div>
                    </div>
                    <label class="flex items-center cursor-pointer relative shrink-0 ml-1">
                      <input type="checkbox" id="fastApproximateSorting" class="sr-only switch-checkbox" />
                      <div class="switch-label block bg-white/10 w-7 h-4 rounded-full transition-colors border border-white/10">
                        <div class="switch-dot absolute left-0.5 top-0.5 bg-zinc-400 w-3 h-3 rounded-full transition-transform"></div>
                      </div>
                    </label>
                  </div>
                </div>

                <div class="grid grid-cols-2 gap-2 mt-2 relative z-10">
                  <button class="py-2.5 rounded-full bg-white/5 hover:bg-white/10 text-zinc-400 text-[9px] uppercase tracking-wider font-semibold transition-all border border-white/5" data-action="initialize">Force Init Engine</button>
                  <button class="py-2.5 rounded-full bg-[#00F0FF]/10 hover:bg-[#00F0FF]/20 text-[#00F0FF] text-[9px] uppercase tracking-wider font-semibold transition-all border border-[#00F0FF]/20" data-action="render_now">Forced Redraw Output</button>
                </div>
              </section>
            </div>

            <script>
              const dllState = document.getElementById('dllState');
              const initState = document.getElementById('initState');
              const sandboxPath = document.getElementById('sandboxPath');
              const statusBar = document.getElementById('statusBar');
              const statusDot = document.getElementById('statusDot');
              const upAxisMode = document.getElementById('upAxisMode');
              const shDegree = document.getElementById('shDegree');
              const shDegreeValue = document.getElementById('shDegreeValue');
              const fastApproximateSorting = document.getElementById('fastApproximateSorting');

              function setStatus(message, level) {
                statusBar.textContent = message;
                if(level === 'ok') {
                  statusBar.className = 'text-[9px] font-mono text-emerald-400 uppercase tracking-widest px-1.5 py-0.5 rounded border transition-colors bg-emerald-500/10 border-emerald-500/20 truncate max-w-[200px]';
                  statusDot.className = 'w-1.5 h-1.5 rounded-full shadow-[0_0_8px_#10B981] bg-emerald-500';
                } else if(level === 'warn') {
                  statusBar.className = 'text-[9px] font-mono text-rose-400 uppercase tracking-widest px-1.5 py-0.5 rounded border transition-colors bg-rose-500/10 border-rose-500/20 truncate max-w-[200px]';
                  statusDot.className = 'w-1.5 h-1.5 rounded-full shadow-[0_0_8px_#F43F5E] bg-rose-500';
                } else {
                  statusBar.className = 'text-[9px] font-mono text-zinc-400 uppercase tracking-widest px-1.5 py-0.5 rounded border transition-colors bg-white/5 border-white/10 truncate max-w-[200px]';
                  statusDot.className = 'w-1.5 h-1.5 rounded-full shadow-[0_0_8px_#FF5400] bg-accent animate-pulse';
                }
              }

              function update(payload) {
                if(payload.loaded) {
                  dllState.innerHTML = '<span class="text-emerald-400">Attached</span>';
                } else {
                  dllState.innerHTML = payload.available ? '<span class="text-amber-400">Disk OK</span>' : '<span class="text-rose-400">Missing</span>';
                }
                
                initState.innerHTML = payload.initialized ? '<span class="text-[#00F0FF]">Active</span>' : '<span class="text-zinc-500">Standby</span>';

                sandboxPath.textContent = payload.sandbox_dir || 'Path unavailable';
                upAxisMode.value = payload.up_axis_mode || 'swap_b';
                shDegree.value = String(payload.sh_render_degree ?? 3);
                shDegreeValue.textContent = shDegree.value;
                fastApproximateSorting.checked = payload.fast_approximate_sorting === true;
                if (payload.message) {
                  setStatus(payload.message, payload.level || '');
                }
              }

              window.GaussianSplattingDialog = { update, setStatus };

              document.querySelectorAll('[data-action]').forEach((button) => {
                button.addEventListener('click', () => {
                  const action = button.getAttribute('data-action');
                  setStatus('Executing...', '');
                  sketchup[action]();
                });
              });

              upAxisMode.addEventListener('change', () => {
                sketchup.set_up_axis_mode(upAxisMode.value);
              });

              shDegree.addEventListener('input', () => {
                shDegreeValue.textContent = shDegree.value;
              });

              shDegree.addEventListener('change', () => {
                setStatus('Updating SH render level...', '');
                sketchup.set_sh_render_degree(shDegree.value);
              });

              fastApproximateSorting.addEventListener('change', () => {
                setStatus('Switching sorting mode...', '');
                sketchup.set_fast_approximate_sorting(fastApproximateSorting.checked ? 'true' : 'false');
              });

              document.addEventListener('DOMContentLoaded', () => {
                sketchup.dialog_ready();
              });
            </script>
          </body>
          </html>
        HTML
      end

      def self.bind_callbacks
        @@dialog.add_action_callback('dialog_ready') do |_ctx|
          push_state
        end

        @@dialog.add_action_callback('set_up_axis_mode') do |_ctx, value|
          mode = GaussianPoints::GaussianSplats.set_up_axis_mode(value)
          label = GaussianPoints::GaussianSplats.orientation_label(mode)
          push_state("Gaussian import orientation set to #{label}. Reload the same GASP/PLY and tell me which variant is correct.", 'ok')
        end

        @@dialog.add_action_callback('set_sh_render_degree') do |_ctx, value|
          degree = GaussianPoints::GaussianSplats.set_sh_render_degree(value)
          GaussianPoints::GaussianSplats.render_splats
          push_state("SH render level set to #{degree}. Compare 0 against 3 live in the same camera view.", 'ok')
        end

        @@dialog.add_action_callback('set_fast_approximate_sorting') do |_ctx, value|
          enabled = GaussianPoints::GaussianSplats.set_fast_approximate_sorting_enabled(value)
          GaussianPoints::GaussianSplats.render_splats
          if enabled
            push_state('Acceleration enabled. Fast legacy sorting is active, so FPS should improve but distant flickering can return.', 'warn')
          else
            push_state('Acceleration disabled. Stable anti-flicker sorting is active.', 'ok')
          end
        end

        @@dialog.add_action_callback('initialize') do |_ctx|
          ok = GaussianPoints::GaussianSplats.init_plugin
          push_state(ok ? 'Gaussian splat renderer initialized.' : 'Failed to initialize Gaussian splats.', ok ? 'ok' : 'warn')
        end

        @@dialog.add_action_callback('analyze_ply') do |_ctx|
          ok = GaussianPoints::GaussianSplats.analyze_ply
          push_state(ok ? 'PLY analysis completed successfully.' : 'PLY analysis was cancelled or failed.', ok ? 'ok' : 'warn')
        end

        @@dialog.add_action_callback('load_scene') do |_ctx|
          filename = UI.openpanel('Choose a Gaussian scene file', '', 'Gaussian Scenes|*.gasp;*.ply|Gaussian GASP|*.gasp|Gaussian PLY|*.ply||')
          if filename
            GaussianPoints::IO::GaussianSceneImporter.load_with_mode_prompt(filename)
            push_state('Choose how to load the selected Gaussian scene.', 'ok')
          else
            push_state('Gaussian scene load was cancelled.', 'warn')
          end
        end

        @@dialog.add_action_callback('render_now') do |_ctx|
          ok = GaussianPoints::GaussianSplats.render_splats
          push_state(ok ? 'Forced redraw sent to the Gaussian renderer.' : 'Renderer is not ready yet.', ok ? 'ok' : 'warn')
        end

        @@dialog.add_action_callback('clear_splats') do |_ctx|
          GaussianPoints::UIparts::RenderItemRegistry.gaussian_items.each do |item|
            GaussianPoints::UIparts::RenderItemRegistry.remove_item(item[:id])
          end
          ok = true
          push_state(ok ? 'Cleared current Gaussian splats.' : 'Nothing was cleared.', ok ? 'ok' : 'warn')
        end
      end

      def self.push_state(message = nil, level = nil)
        payload = GaussianPoints::GaussianSplats.status_payload.dup
        payload[:message] = message if message
        payload[:level] = level if level
        @@dialog.execute_script("window.GaussianSplattingDialog.update(#{JSON.generate(payload)})") if @@dialog
      end
    end
  end
end
