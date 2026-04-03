# ui/delete_dialog.rb

module GaussianPoints
  module UIparts

    module DeleteDialog

      @@dialog = nil

      def self.show_dialog
        if @@dialog && @@dialog.visible?
          @@dialog.bring_to_front
          return
        end

        opts = {
          dialog_title: "Delete Points?",
          scrollable: false,
          resizable: false,
          width: 320,
          height: 160
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
                background-color: #fff;
              }
              .container {
                display: flex; flex-direction: column;
                justify-content: center; align-items: center;
                height: 100%;
              }
              h2 { margin: 10px; }
              .btn {
                margin: 5px; padding: 8px 20px;
                background-color: #f90; border: none; border-radius: 4px;
                color: #fff; font-size: 14px; cursor: pointer;
                white-space: nowrap;
              }
              .btn:hover {
                background-color: #fa0;
              }
              .btn.no {
                background-color: #bbb;
                color: #333;
              }
              .btn.no:hover {
                background-color: #999;
              }
            </style>
          </head>
          <body>
            <div class="container">
              <h2>Delete all points?</h2>
              <div>
                <button class="btn yes" onclick="sketchup.delete_confirm(true)">Yes</button>
                <button class="btn no"  onclick="sketchup.delete_confirm(false)">No</button>
              </div>
            </div>
          </body>
          </html>
        HTML

        @@dialog.set_html(html)

        @@dialog.add_action_callback("delete_confirm") do |_ctx, yesOrNo|
          if yesOrNo.to_s == "true"
            overlay = GaussianPoints.overlay
            overlay.clear_points if overlay
            GaussianPoints::UIparts::RenderItemRegistry.clear_all if defined?(GaussianPoints::UIparts::RenderItemRegistry)
            GaussianPoints::Hook.clear_pointcloud if defined?(GaussianPoints::Hook)
            GaussianPoints::GaussianSplats.clear_splats if defined?(GaussianPoints::GaussianSplats)
          end
          @@dialog.close
        end

        @@dialog.center
        @@dialog.show
      end

    end
  end
end
