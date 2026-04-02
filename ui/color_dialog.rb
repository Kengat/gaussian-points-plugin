# ui/color_dialog.rb

module GaussianPoints
  module UIparts

    module ColorDialog

      @@dialog = nil

      def self.show_dialog
        if @@dialog && @@dialog.visible?
          @@dialog.bring_to_front
          return
        end

        opts = {
          dialog_title: "Pick Color",
          scrollable: false,
          resizable: false,
          width: 350,
          height: 470
        }
        @@dialog = UI::HtmlDialog.new(opts)

        wheel_path = File.join(GaussianPoints::PLUGIN_DIR, "ui", "icons", "color_wheel.png")
        wheel_url  = "file:///" + wheel_path.gsub("\\","/")

        html = <<-HTML
          <html>
          <head>
            <meta charset="UTF-8">
            <style>
              body { margin: 10px; padding: 0; font-family: sans-serif; }
              #colorWheel {
                width: 300px; height: 300px; cursor: crosshair;
              }
              .btn {
                margin-top: 10px; padding: 8px 16px;
                background-color: #f90; border: none; border-radius: 4px;
                color: #fff; cursor: pointer;
              }
              .btn:hover { background-color: #fa0; }
            </style>
          </head>
          <body>
            <h3>Pick a Color</h3>
            <canvas id="colorWheel" width="300" height="300"></canvas>
            <div id="info"></div>
            <button class="btn" id="applyBtn">Apply</button>

            <script>
              const canvas = document.getElementById('colorWheel');
              const ctx    = canvas.getContext('2d');
              const info   = document.getElementById('info');

              let currentColor = {r:0, g:0, b:0};

              const img = new Image();
              img.src = "#{wheel_url}";
              img.onload = function(){
                ctx.drawImage(img, 0, 0, 300, 300);
              };

              canvas.addEventListener('click', (e)=>{
                let rect = canvas.getBoundingClientRect();
                let x = e.clientX - rect.left;
                let y = e.clientY - rect.top;

                let data = ctx.getImageData(x,y,1,1).data;
                currentColor.r = data[0];
                currentColor.g = data[1];
                currentColor.b = data[2];
                info.textContent = "R="+data[0]+" G="+data[1]+" B="+data[2];
              });

              document.getElementById('applyBtn').addEventListener('click', ()=>{
                sketchup.set_color(currentColor.r, currentColor.g, currentColor.b);
              });
            </script>
          </body>
          </html>
        HTML

        @@dialog.set_html(html)

        @@dialog.add_action_callback("set_color") do |_ctx, rS, gS, bS|
          r = rS.to_i
          g = gS.to_i
          b = bS.to_i
          overlay = GaussianPoints.overlay
          if overlay
            overlay.visible_color.red   = r
            overlay.visible_color.green = g
            overlay.visible_color.blue  = b
            # (Если хотите, меняйте и hidden_color)
            Sketchup.active_model.active_view.invalidate
          end
          @@dialog.close
        end

        @@dialog.center
        @@dialog.show
      end

    end
  end
end
