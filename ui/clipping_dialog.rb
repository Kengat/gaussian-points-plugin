# ui/clipping_dialog.rb

module GaussianPoints
  module UIparts

    module ClippingDialog

      @@dialog = nil

      def self.show_dialog
        if @@dialog && @@dialog.visible?
          @@dialog.bring_to_front
          return
        end

        opts = {
          dialog_title: "Clip Box",
          scrollable: false,
          resizable: false,
          width: 280,
          height: 220
        }
        @@dialog = UI::HtmlDialog.new(opts)

        html = <<-HTML
          <html>
          <head>
            <meta charset="UTF-8">
            <style>
              body { margin:0; padding:10px; font-family:sans-serif; }
              .btn {
                display:block; margin:8px 0; padding:8px 14px;
                background:#f90; border:none; border-radius:4px; color:#fff;
                font-size:14px; cursor:pointer;
              }
              .btn:hover {
                background:#fa0;
              }
            </style>
          </head>
          <body>
            <button class="btn" onclick="sketchup.toggle_box_visibility()">Show/Hide Box</button>
            <button class="btn" onclick="sketchup.refresh_clip()">Refresh Clip</button>
            <button class="btn" onclick="sketchup.reset_box()">Reset Box</button>
            <hr/>
            <button class="btn" onclick="sketchup.remove_clip()">Remove Clip</button>
          </body>
          </html>
        HTML

        @@dialog.set_html(html)

        @@dialog.add_action_callback("toggle_box_visibility") do |_ctx|
          GaussianPoints::UIparts::ClippingManager.toggle_box_visibility
        end

        @@dialog.add_action_callback("refresh_clip") do |_ctx|
          GaussianPoints::UIparts::ClippingManager.apply_clipping
        end

        @@dialog.add_action_callback("reset_box") do |_ctx|
          GaussianPoints::UIparts::ClippingManager.reset_box_position
        end

        @@dialog.add_action_callback("remove_clip") do |_ctx|
          GaussianPoints::UIparts::ClippingManager.disable_clip
          @@dialog.close
        end

        @@dialog.center
        @@dialog.show
      end

      def self.close_if_open
        if @@dialog && @@dialog.visible?
          @@dialog.close
        end
      end

    end
  end
end
