require 'json'

module GaussianPoints
  module UIparts
    module ImportProgressDialog
      extend self

      @dialog = nil
      @shown_once = false
      @last_payload = nil

      def show(payload = nil)
        ensure_dialog
        @dialog.center unless @shown_once
        @dialog.show
        @shown_once = true
        update(payload) if payload
      end

      def close
        @dialog&.close
      rescue StandardError
        nil
      end

      def update(payload)
        @last_payload = payload if payload
        return unless @dialog && @dialog.visible?

        @dialog.execute_script("window.ImportProgressDialog.update(#{JSON.generate(@last_payload || {})})")
      end

      private

      def ensure_dialog
        return if @dialog

        @shown_once = false
        @dialog = UI::HtmlDialog.new(
          dialog_title: 'Point Cloud Import',
          width: 520,
          height: 420,
          resizable: true,
          scrollable: false,
          style: UI::HtmlDialog::STYLE_DIALOG
        )
        @dialog.set_html(dialog_html)
        bind_callbacks
      end

      def bind_callbacks
        @dialog.add_action_callback('dialog_ready') do |_ctx|
          update(@last_payload)
          GaussianPoints::IO::Importer.on_import_dialog_ready
        end

        @dialog.add_action_callback('choose_mode') do |_ctx, mode|
          GaussianPoints::IO::Importer.begin_pending_import(mode.to_s)
        end

        @dialog.add_action_callback('cancel_import') do |_ctx|
          GaussianPoints::IO::Importer.cancel_active_import
        end
      end

      def dialog_html
        <<~HTML
          <!DOCTYPE html>
          <html>
          <head>
            <meta charset="UTF-8">
            <style>
              :root {
                --bg: #111315;
                --panel: #1b1f23;
                --muted: #8d98a5;
                --text: #f4f7fb;
                --accent: #f28c28;
                --accent-2: #ffd07a;
                --danger: #d75a5a;
                --border: #2c3238;
              }
              * { box-sizing: border-box; }
              body {
                margin: 0;
                padding: 18px;
                background: radial-gradient(circle at top left, #20262b 0%, var(--bg) 58%);
                color: var(--text);
                font: 13px/1.45 "Segoe UI", sans-serif;
              }
              .wrap {
                display: flex;
                flex-direction: column;
                gap: 14px;
                min-height: 100%;
              }
              .panel {
                background: color-mix(in srgb, var(--panel) 92%, #fff 8%);
                border: 1px solid var(--border);
                border-radius: 14px;
                padding: 16px;
                box-shadow: 0 16px 32px rgba(0,0,0,0.22);
              }
              .title { font-size: 18px; font-weight: 700; margin-bottom: 4px; }
              .subtitle { color: var(--muted); }
              .meta {
                display: grid;
                grid-template-columns: 120px 1fr;
                gap: 8px 12px;
                margin-top: 12px;
              }
              .meta .label { color: var(--muted); }
              .progress {
                height: 12px;
                border-radius: 999px;
                overflow: hidden;
                background: rgba(255,255,255,0.08);
                border: 1px solid rgba(255,255,255,0.06);
              }
              .bar {
                width: 0%;
                height: 100%;
                background: linear-gradient(90deg, var(--accent), var(--accent-2));
                transition: width 0.18s ease;
              }
              .row { display: flex; gap: 10px; flex-wrap: wrap; }
              .btn {
                border: 0;
                border-radius: 10px;
                padding: 11px 14px;
                cursor: pointer;
                font-weight: 700;
              }
              .btn.primary { background: var(--accent); color: #111; }
              .btn.secondary { background: #27303a; color: var(--text); }
              .btn.ghost { background: transparent; color: var(--muted); border: 1px solid var(--border); }
              .btn.danger { background: #3a2020; color: #ffd8d8; border: 1px solid #5a2c2c; }
              .hidden { display: none !important; }
              .status {
                font-size: 14px;
                font-weight: 600;
                margin-bottom: 8px;
              }
              .detail { color: var(--muted); min-height: 20px; }
              .footer { margin-top: auto; display: flex; justify-content: space-between; align-items: center; color: var(--muted); }
            </style>
          </head>
          <body>
            <div class="wrap">
              <div class="panel">
                <div class="title" id="title">Point Cloud Import</div>
                <div class="subtitle" id="subtitle">Preparing import</div>
                <div class="meta">
                  <div class="label">File</div><div id="file_name">--</div>
                  <div class="label">Type</div><div id="file_type">--</div>
                  <div class="label">Points</div><div id="point_count">--</div>
                  <div class="label">Target</div><div id="target_mode">--</div>
                </div>
              </div>

              <div class="panel">
                <div class="status" id="status">Waiting for action</div>
                <div class="detail" id="detail">Choose how to load the selected file.</div>
                <div class="progress"><div id="progress_bar" class="bar"></div></div>
                <div style="margin-top:10px; color: var(--muted);" id="progress_text">0%</div>
              </div>

              <div class="panel hidden" id="selection_panel">
                <div class="row">
                  <button class="btn primary" onclick="sketchup.choose_mode('gasp')">Create Fast .gasp</button>
                  <button class="btn secondary" onclick="sketchup.choose_mode('direct')">Load Directly</button>
                </div>
                <div style="margin-top:10px; color: var(--muted);">
                  Direct load keeps the source file path. Creating <code>.gasp</code> builds a fast cached project and loads that instead.
                </div>
              </div>

              <div class="footer">
                <div id="phase_label">Idle</div>
                <button class="btn ghost" onclick="sketchup.cancel_import()">Close</button>
              </div>
            </div>
            <script>
              window.ImportProgressDialog = {
                update(payload) {
                  payload = payload || {};
                  const set = (id, value) => {
                    const el = document.getElementById(id);
                    if (el && value !== undefined && value !== null) el.textContent = value;
                  };
                  set('title', payload.title || 'Point Cloud Import');
                  set('subtitle', payload.subtitle || 'Preparing import');
                  set('file_name', payload.file_name || '--');
                  set('file_type', payload.file_type || '--');
                  set('point_count', payload.point_count_label || '--');
                  set('target_mode', payload.target_mode || '--');
                  set('status', payload.status || 'Waiting for action');
                  set('detail', payload.detail || '');
                  set('progress_text', payload.progress_label || '0%');
                  set('phase_label', payload.phase || 'Idle');
                  document.getElementById('progress_bar').style.width = `${payload.progress_percent || 0}%`;
                  document.getElementById('selection_panel').classList.toggle('hidden', !payload.show_choices);
                }
              };
              document.addEventListener('DOMContentLoaded', () => sketchup.dialog_ready());
            </script>
          </body>
          </html>
        HTML
      end
    end
  end
end

