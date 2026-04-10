require 'json'

module GaussianPoints
  module UIparts
    module GaussianSplatLoadDialog
      extend self

      @dialog = nil
      @callback = nil
      @payload = nil

      def choose(filename, &callback)
        @callback = callback
        @payload = {
          title: 'Gaussian Scene Import',
          file_name: File.basename(filename.to_s),
          file_type: File.extname(filename.to_s).downcase.sub('.', '').upcase,
          detail: 'Choose how to load this splat file.'
        }
        ensure_dialog
        @dialog.center
        @dialog.show
        update
      end

      private

      def ensure_dialog
        return if @dialog

        @dialog = UI::HtmlDialog.new(
          dialog_title: 'Gaussian Scene Import',
          width: 540,
          height: 340,
          resizable: false,
          scrollable: false,
          style: UI::HtmlDialog::STYLE_DIALOG
        )
        @dialog.set_html(dialog_html)
        bind_callbacks
      end

      def bind_callbacks
        @dialog.add_action_callback('dialog_ready') { |_ctx| update }
        @dialog.add_action_callback('choose_mode') do |_ctx, mode|
          callback = @callback
          @callback = nil
          @dialog.close
          callback.call(mode.to_s) if callback
        end
        @dialog.add_action_callback('cancel_import') do |_ctx|
          @callback = nil
          @dialog.close
        end
      end

      def update
        return unless @dialog && @dialog.visible?

        @dialog.execute_script("window.GaussianSplatLoadDialog.update(#{JSON.generate(@payload || {})})")
      end

      def dialog_html
        <<~HTML
          <!DOCTYPE html>
          <html>
          <head>
            <meta charset="UTF-8">
            <style>
              * { box-sizing: border-box; }
              body {
                margin: 0;
                padding: 22px;
                background: #050505;
                color: #fafafa;
                font: 13px/1.45 "Segoe UI", sans-serif;
              }
              .card {
                border: 1px solid rgba(255,255,255,0.10);
                background: #101016;
                border-radius: 16px;
                padding: 22px;
                box-shadow: 0 24px 70px rgba(0,0,0,0.55);
              }
              .eyebrow {
                color: #71717a;
                font-size: 10px;
                font-weight: 800;
                letter-spacing: 0.14em;
                text-transform: uppercase;
                margin-bottom: 10px;
              }
              .title {
                font-size: 24px;
                font-weight: 700;
                letter-spacing: -0.02em;
                margin-bottom: 8px;
              }
              .file {
                color: #a1a1aa;
                margin-bottom: 18px;
                word-break: break-word;
              }
              .choices {
                display: grid;
                grid-template-columns: 1fr 1fr;
                gap: 12px;
              }
              button {
                border-radius: 8px;
                border: 1px solid rgba(255,255,255,0.10);
                padding: 15px;
                text-align: left;
                cursor: pointer;
                color: #fff;
                font: inherit;
              }
              .primary { background: #ff5400; border-color: rgba(255,84,0,0.42); }
              .secondary { background: rgba(255,255,255,0.035); }
              .label { font-weight: 800; margin-bottom: 4px; }
              .hint { color: rgba(255,255,255,0.74); font-size: 12px; }
              .secondary .hint { color: #a1a1aa; }
              .footer { display: flex; justify-content: flex-end; margin-top: 18px; }
              .cancel {
                width: auto;
                padding: 9px 14px;
                color: #e4e4e7;
                background: rgba(255,255,255,0.03);
              }
            </style>
          </head>
          <body>
            <div class="card">
              <div class="eyebrow">Gaussian Scene Import</div>
              <div class="title">Choose load mode</div>
              <div class="file" id="file_name">Selected file</div>
              <div class="choices">
                <button class="primary" onclick="sketchup.choose_mode('gasp')">
                  <div class="label">Create / Use GASP</div>
                  <div class="hint">Fast cache for instant reloads.</div>
                </button>
                <button class="secondary" onclick="sketchup.choose_mode('direct')">
                  <div class="label">Load Directly</div>
                  <div class="hint">Use the selected file without building a cache.</div>
                </button>
              </div>
              <div class="footer">
                <button class="cancel" onclick="sketchup.cancel_import()">Cancel</button>
              </div>
            </div>
            <script>
              window.GaussianSplatLoadDialog = {
                update(payload) {
                  document.getElementById('file_name').textContent = payload.file_name || 'Selected file';
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
