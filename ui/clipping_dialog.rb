# ui/clipping_dialog.rb

module GaussianPoints
  module UIparts

    module ClippingDialog

      @@dialog = nil

      def self.show_dialog
        if @@dialog && @@dialog.visible?
          refresh_status
          @@dialog.bring_to_front
          return
        end

        opts = {
          dialog_title: "Clip Box",
          scrollable: false,
          resizable: false,
          width: 320,
          height: 260
        }
        @@dialog = UI::HtmlDialog.new(opts)

        html = <<-HTML
          <html>
          <head>
            <meta charset="UTF-8">
            <style>
              body { margin:0; padding:10px; font-family:sans-serif; }
              .status {
                padding:10px 12px; border-radius:8px; margin-bottom:10px;
                background:#1e1f22; color:#f2f2f2; font-size:13px;
              }
              .btn {
                display:block; width:100%; margin:8px 0; padding:8px 14px;
                background:#f90; border:none; border-radius:6px; color:#fff;
                font-size:14px; cursor:pointer;
              }
              .btn:hover {
                background:#fa0;
              }
            </style>
          </head>
          <body>
            <div id="status" class="status">Clip box status: --</div>
            <button class="btn" onclick="sketchup.toggle_gizmo()">Toggle Gizmo</button>
            <button class="btn" onclick="sketchup.toggle_box_visibility()">Show / Hide Box</button>
            <button class="btn" onclick="sketchup.reset_box()">Fit To Scene</button>
            <button class="btn" onclick="sketchup.refresh_clip()">Refresh Clip</button>
            <hr/>
            <button class="btn" onclick="sketchup.remove_clip()">Remove Clip</button>
            <script>
              function updateStatus(text) {
                document.getElementById('status').textContent = text;
              }
            </script>
          </body>
          </html>
        HTML

        @@dialog.set_html(html)

        @@dialog.add_action_callback("toggle_gizmo") do |_ctx|
          GaussianPoints::UIparts::ClippingManager.toggle_gizmo
          refresh_status
        end

        @@dialog.add_action_callback("toggle_box_visibility") do |_ctx|
          GaussianPoints::UIparts::ClippingManager.toggle_box_visibility
          refresh_status
        end

        @@dialog.add_action_callback("refresh_clip") do |_ctx|
          GaussianPoints::UIparts::ClippingManager.apply_clipping
          refresh_status
        end

        @@dialog.add_action_callback("reset_box") do |_ctx|
          GaussianPoints::UIparts::ClippingManager.reset_box_position
          refresh_status
        end

        @@dialog.add_action_callback("remove_clip") do |_ctx|
          GaussianPoints::UIparts::ClippingManager.remove_clip
          @@dialog.close
        end

        @@dialog.center
        @@dialog.show
        refresh_status
      end

      def self.close_if_open
        if @@dialog && @@dialog.visible?
          @@dialog.close
        end
      end

      def self.refresh_status
        return unless @@dialog && @@dialog.visible?

        label = GaussianPoints::UIparts::ClippingManager.status_label
        @@dialog.execute_script("updateStatus(#{label.inspect})")
      end

    end
  end
end
