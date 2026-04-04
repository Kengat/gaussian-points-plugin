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
          width: 520,
          height: 560,
          scrollable: false,
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
          <html>
          <head>
            <meta charset="UTF-8">
            <style>
              :root {
                --panel: rgba(255, 250, 243, 0.92);
                --ink: #211a15;
                --muted: #6c6158;
                --line: rgba(78, 62, 48, 0.14);
                --accent: #b85c38;
                --accent-strong: #8f3f1f;
                --accent-soft: #f3d7bf;
                --shadow: 0 22px 55px rgba(49, 30, 18, 0.14);
              }

              * { box-sizing: border-box; }
              html, body {
                margin: 0;
                width: 100%;
                height: 100%;
                overflow: hidden;
                font-family: Georgia, "Palatino Linotype", serif;
                color: var(--ink);
                background:
                  radial-gradient(circle at top left, rgba(255,255,255,0.78), transparent 36%),
                  linear-gradient(145deg, #efe3d3 0%, #e7ddcf 48%, #f7f2ea 100%);
              }

              body {
                display: flex;
                flex-direction: column;
              }

              .hero {
                padding: 22px 24px 18px;
                border-bottom: 1px solid var(--line);
                background:
                  linear-gradient(135deg, rgba(184,92,56,0.16), rgba(255,255,255,0) 68%),
                  linear-gradient(180deg, rgba(255,255,255,0.8), rgba(255,255,255,0.22));
              }

              .eyebrow {
                font-size: 11px;
                letter-spacing: 0.22em;
                text-transform: uppercase;
                color: var(--muted);
                margin-bottom: 8px;
              }

              .title {
                font-size: 30px;
                line-height: 1;
                margin: 0 0 10px;
              }

              .subtitle {
                margin: 0;
                max-width: 420px;
                font-size: 14px;
                line-height: 1.45;
                color: var(--muted);
              }

              .content {
                flex: 1;
                overflow: auto;
                padding: 18px 18px 20px;
              }

              .panel {
                background: var(--panel);
                border: 1px solid var(--line);
                border-radius: 18px;
                box-shadow: var(--shadow);
                padding: 16px;
                margin-bottom: 14px;
              }

              .panel h2 {
                margin: 0 0 10px;
                font-size: 18px;
              }

              .panel p {
                margin: 0;
                color: var(--muted);
                font-size: 13px;
                line-height: 1.5;
              }

              .status-grid {
                display: grid;
                grid-template-columns: repeat(2, minmax(0, 1fr));
                gap: 10px;
                margin-top: 14px;
              }

              .status-card {
                border: 1px solid var(--line);
                border-radius: 14px;
                background: rgba(255,255,255,0.72);
                padding: 11px 12px;
              }

              .status-label {
                font-size: 11px;
                text-transform: uppercase;
                letter-spacing: 0.14em;
                color: var(--muted);
                margin-bottom: 8px;
              }

              .status-value {
                font-size: 15px;
                font-weight: bold;
              }

              .pill {
                display: inline-flex;
                align-items: center;
                gap: 7px;
                padding: 7px 11px;
                border-radius: 999px;
                background: var(--accent-soft);
                color: var(--accent-strong);
                font-size: 12px;
                margin-top: 12px;
              }

              .actions {
                display: grid;
                grid-template-columns: repeat(2, minmax(0, 1fr));
                gap: 12px;
                margin-top: 14px;
              }

              button {
                border: none;
                border-radius: 14px;
                cursor: pointer;
                font: inherit;
                transition: transform 0.12s ease, box-shadow 0.12s ease, background 0.12s ease;
              }

              button:hover {
                transform: translateY(-1px);
              }

              .primary {
                padding: 16px 14px;
                background: linear-gradient(135deg, var(--accent), #d07f52);
                color: white;
                text-align: left;
                box-shadow: 0 10px 20px rgba(143, 63, 31, 0.22);
              }

              .primary strong,
              .secondary strong {
                display: block;
                font-size: 15px;
                margin-bottom: 4px;
              }

              .primary span,
              .secondary span {
                display: block;
                font-size: 12px;
                line-height: 1.4;
                opacity: 0.95;
              }

              .secondary {
                padding: 14px;
                text-align: left;
                background: rgba(255,255,255,0.88);
                color: var(--ink);
                border: 1px solid var(--line);
              }

              .secondary-row {
                display: grid;
                grid-template-columns: repeat(3, minmax(0, 1fr));
                gap: 10px;
                margin-top: 10px;
              }

              .mini {
                padding: 12px 10px;
                background: rgba(255,255,255,0.88);
                border: 1px solid var(--line);
                color: var(--ink);
              }

              .mini.warn {
                background: rgba(184,92,56,0.08);
              }

              .footer {
                font-size: 12px;
                color: var(--muted);
                line-height: 1.45;
                margin-top: 10px;
              }

              .field {
                margin-top: 14px;
              }

              .field label {
                display: block;
                font-size: 12px;
                text-transform: uppercase;
                letter-spacing: 0.14em;
                color: var(--muted);
                margin-bottom: 8px;
              }

              .field select {
                width: 100%;
                padding: 12px 14px;
                border-radius: 12px;
                border: 1px solid var(--line);
                background: rgba(255,255,255,0.9);
                color: var(--ink);
                font: inherit;
              }

              .slider-wrap {
                margin-top: 12px;
                padding: 14px;
                border-radius: 14px;
                border: 1px solid var(--line);
                background: rgba(255,255,255,0.76);
              }

              .slider-head {
                display: flex;
                align-items: center;
                justify-content: space-between;
                margin-bottom: 10px;
                font-size: 13px;
              }

              .slider-value {
                font-weight: bold;
                color: var(--accent-strong);
              }

              input[type="range"] {
                width: 100%;
              }

              .slider-steps {
                display: grid;
                grid-template-columns: repeat(4, minmax(0, 1fr));
                gap: 6px;
                margin-top: 8px;
                font-size: 11px;
                color: var(--muted);
                text-align: center;
              }

              .status-bar {
                margin-top: 14px;
                padding: 12px 14px;
                border-radius: 14px;
                background: rgba(255,255,255,0.8);
                border: 1px solid var(--line);
                font-size: 13px;
                color: var(--ink);
              }

              .status-bar.ok {
                border-color: rgba(47, 125, 91, 0.24);
                background: rgba(47, 125, 91, 0.08);
              }

              .status-bar.warn {
                border-color: rgba(155, 92, 22, 0.24);
                background: rgba(155, 92, 22, 0.08);
              }

              .toggle-card {
                margin-top: 12px;
                padding: 14px;
                border-radius: 14px;
                border: 1px solid var(--line);
                background: rgba(255,255,255,0.76);
              }

              .toggle-row {
                display: flex;
                align-items: center;
                justify-content: space-between;
                gap: 14px;
              }

              .toggle-copy strong {
                display: block;
                font-size: 14px;
                margin-bottom: 4px;
              }

              .toggle-copy span {
                display: block;
                color: var(--muted);
                font-size: 12px;
                line-height: 1.45;
              }

              .switch {
                position: relative;
                display: inline-flex;
                width: 52px;
                height: 30px;
                flex: 0 0 auto;
              }

              .switch input {
                opacity: 0;
                width: 0;
                height: 0;
              }

              .slider {
                position: absolute;
                inset: 0;
                cursor: pointer;
                background: rgba(108, 97, 88, 0.25);
                border-radius: 999px;
                transition: background 0.15s ease;
              }

              .slider:before {
                content: "";
                position: absolute;
                height: 24px;
                width: 24px;
                left: 3px;
                top: 3px;
                background: white;
                border-radius: 50%;
                box-shadow: 0 4px 10px rgba(33, 26, 21, 0.2);
                transition: transform 0.15s ease;
              }

              .switch input:checked + .slider {
                background: linear-gradient(135deg, var(--accent), #d07f52);
              }

              .switch input:checked + .slider:before {
                transform: translateX(22px);
              }
            </style>
          </head>
          <body>
            <section class="hero">
              <div class="eyebrow">Unified Tooling</div>
              <h1 class="title">Gaussian Splatting</h1>
              <p class="subtitle">
                One control room for the current splat workflow: initialize the renderer,
                inspect a PLY, load splats into the active SketchUp scene, force redraw, or clear them.
              </p>
            </section>

            <div class="content">
              <section class="panel">
                <h2>Runtime Status</h2>
                <p>
                  This panel reflects the current integrated Gaussian runtime that lives inside the main
                  <strong>Gaussian Points</strong> plugin.
                </p>

                <div class="status-grid">
                  <div class="status-card">
                    <div class="status-label">Bridge / DLLs</div>
                    <div class="status-value" id="dllState">Checking...</div>
                  </div>
                  <div class="status-card">
                    <div class="status-label">Renderer State</div>
                    <div class="status-value" id="initState">Checking...</div>
                  </div>
                </div>

                <div class="pill" id="sandboxPath">Preparing path...</div>
                <div class="status-bar" id="statusBar">Open this window from the toolbar whenever you want to work with splats.</div>
              </section>

              <section class="panel">
                <h2>PLY Workflow</h2>
                <p>Use the same actions as before, but now from the root toolbar instead of a separate sandbox plugin.</p>
                <div class="field">
                  <label for="upAxisMode">Import Orientation Test Mode</label>
                  <select id="upAxisMode">
                    <option value="legacy">1. Y-up (original/Postshot)</option>
                    <option value="swap_a">2. Z-up inverted</option>
                    <option value="swap_b">3. Z-up (default)</option>
                  </select>
                </div>
                <div class="actions">
                  <button class="primary" data-action="load_ply">
                    <strong>Load Gaussian PLY</strong>
                    <span>Choose a .ply file and load splats into the active scene.</span>
                  </button>
                  <button class="secondary" data-action="analyze_ply">
                    <strong>Analyze PLY</strong>
                    <span>Run the importer check without changing the rendered splats.</span>
                  </button>
                </div>
              </section>

              <section class="panel">
                <h2>Scene Controls</h2>
                <p>Useful while iterating inside SketchUp without reopening the dialog.</p>
                <div class="slider-wrap">
                  <div class="slider-head">
                    <span>SH Render Level</span>
                    <span class="slider-value" id="shDegreeValue">3</span>
                  </div>
                  <input id="shDegree" type="range" min="0" max="3" step="1" value="3" />
                  <div class="slider-steps">
                    <span>0 · DC</span>
                    <span>1</span>
                    <span>2</span>
                    <span>3 · Full</span>
                  </div>
                </div>
                <div class="toggle-card">
                  <div class="toggle-row">
                    <div class="toggle-copy">
                      <strong>Acceleration</strong>
                      <span>Uses the old fast approximate sorting path for higher FPS. Can reintroduce flickering at distance.</span>
                    </div>
                    <label class="switch">
                      <input id="fastApproximateSorting" type="checkbox" />
                      <span class="slider"></span>
                    </label>
                  </div>
                </div>
                <div class="secondary-row">
                  <button class="mini" data-action="initialize">Initialize</button>
                  <button class="mini" data-action="render_now">Show Now</button>
                  <button class="mini warn" data-action="clear_splats">Clear</button>
                </div>
                <div class="footer">
                  I kept the implementation files inside <code>sandbox</code> for now to avoid risky file churn.
                  The user-facing entrypoint is unified here in the main plugin.
                </div>
              </section>
            </div>

            <script>
              const dllState = document.getElementById('dllState');
              const initState = document.getElementById('initState');
              const sandboxPath = document.getElementById('sandboxPath');
              const statusBar = document.getElementById('statusBar');
              const upAxisMode = document.getElementById('upAxisMode');
              const shDegree = document.getElementById('shDegree');
              const shDegreeValue = document.getElementById('shDegreeValue');
              const fastApproximateSorting = document.getElementById('fastApproximateSorting');

              function setStatus(message, level) {
                statusBar.textContent = message;
                statusBar.className = 'status-bar' + (level ? ' ' + level : '');
              }

              function update(payload) {
                dllState.textContent = payload.loaded ? 'Loaded' : (payload.available ? 'Found on disk' : 'Missing pieces');
                initState.textContent = payload.initialized ? 'Initialized' : 'Idle';
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
                  setStatus('Working...', '');
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
                setStatus('Switching render acceleration mode...', '');
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
          push_state("Gaussian import orientation set to #{label}. Reload the same PLY and tell me which variant is correct.", 'ok')
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

        @@dialog.add_action_callback('load_ply') do |_ctx|
          filename = UI.openpanel('Choose a Gaussian PLY file', '', 'PLY Files|*.ply||')
          item =
            if filename
              GaussianPoints::UIparts::RenderItemRegistry.register_gaussian_file(
                name: File.basename(filename),
                filename: filename
              )
            end
          push_state(item ? 'Gaussian splats loaded into the current scene.' : 'PLY load was cancelled or failed.', item ? 'ok' : 'warn')
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
