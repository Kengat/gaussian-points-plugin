# ui/settings_dialog.rb

module GaussianPoints
  module UIparts

    module SettingsDialog

      @@dialog = nil

      def self.show_dialog
        # Если окно уже открыто, приносим на передний план
        if @@dialog && @@dialog.visible?
          @@dialog.bring_to_front
          return
        end

        opts = {
          :dialog_title => "Settings",
          :scrollable   => false,
          :resizable    => false,
          :width        => 400,
          :height       => 240
        }
        @@dialog = UI::HtmlDialog.new(opts)

        html = <<-HTML
          <html>
          <head>
            <meta charset="UTF-8">
            <style>
              body {
                margin: 0; padding: 0;
                font-family: sans-serif;
                display: flex; flex-direction: column;
                height: 100%;
              }
              .header {
                background: linear-gradient(120deg, #ccf, #88f);
                height: 60px; display: flex; align-items: center;
                padding: 0 15px; color: #333; font-size: 20px;
              }
              .content {
                flex: 1; padding: 10px 15px;
              }
              .row {
                margin-bottom: 15px;
              }
              .row label {
                display: block; font-weight: bold;
                margin-bottom: 5px;
              }
              input[type=range] {
                width: 100%;
              }
              .footer {
                padding: 10px 15px; text-align: right;
                background: #eee;
              }
              .btn {
                padding: 8px 16px; margin-left: 10px;
                border: none; border-radius: 4px;
                cursor: pointer; font-size: 14px;
              }
              .btn.stop {
                background-color: #f66; color: #fff;
              }
              .btn.stop:hover {
                background-color: #f33;
              }
              .btn.apply {
                background-color: #3c9; color: #fff;
              }
              .btn.apply:hover {
                background-color: #2a8;
              }
            </style>
          </head>
          <body>
            <div class="header">Settings</div>
            <div class="content">
              <div class="row">
                <label for="downsample">Downsample (0 = no points, 100 = all points)</label>
                <input type="range" id="downsample" min="0" max="100" value="100" />
                <span id="dsVal">100</span>%
              </div>
            </div>
            <div class="footer">
              <button class="btn apply" onclick="applySettings()">Apply</button>
              <button class="btn stop" onclick="stopPlugin()">Stop Plugin</button>
            </div>

            <script>
              const dsSlider = document.getElementById('downsample');
              const dsVal    = document.getElementById('dsVal');

              dsSlider.addEventListener('input', () => {
                dsVal.textContent = dsSlider.value;
              });

              function applySettings() {
                let ds = dsSlider.value;
                sketchup.apply_downsample(ds);
              }
              function stopPlugin() {
                sketchup.stop_plugin();
              }
            </script>
          </body>
          </html>
        HTML

        @@dialog.set_html(html)

        # Callback: apply_downsample
        @@dialog.add_action_callback("apply_downsample") do |_ctx, ds_str|
          ds_i = ds_str.to_i
          overlay = GaussianPoints.overlay
          if overlay
            overlay.apply_downsample(ds_i / 100.0)
          end
        end

        # Callback: stop_plugin
        @@dialog.add_action_callback("stop_plugin") do |_ctx|
          GaussianPoints.stop_plugin
          @@dialog.close
        end

        @@dialog.center
        @@dialog.show
      end

    end
  end
end
