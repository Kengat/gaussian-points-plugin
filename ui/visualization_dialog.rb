# ui/visualization_dialog.rb

module GaussianPoints
  module UIparts

    module VisualizationDialog

      @@dialog = nil

      def self.show_dialog
        if @@dialog && @@dialog.visible?
          @@dialog.bring_to_front
          return
        end

        opts = {
          dialog_title: "Visualization Settings",
          width: 420,
          height: 360,
          scrollable: false,
          resizable: false
        }
        @@dialog = UI::HtmlDialog.new(opts)

        html = <<-HTML
          <html>
          <head>
            <meta charset="UTF-8">
            <style>
              body {
                margin:0; padding:0; font-family:sans-serif; display:flex; flex-direction:column; height:100%;
              }
              .top-bar {
                background: linear-gradient(120deg, #ffee77, #ffcc00);
                height:60px; display:flex; align-items:center; padding:0 15px;
                color:#333; font-size:24px; font-weight:bold;
              }
              .content {
                flex:1; padding:10px 15px; overflow:auto;
              }
              .row { margin-bottom:12px; }
              .row label {
                display:block; font-weight:bold; margin-bottom:5px;
              }
              input[type=range]{ width:100%; }
              .btn {
                padding:8px 16px; background:#f90; border:none; border-radius:4px;
                color:#fff; cursor:pointer; font-size:14px;
              }
              .btn:hover { background:#fa0; }
              .inline { display:inline-block; margin-left:10px; }
            </style>
          </head>
          <body>
            <div class="top-bar">Visualization</div>
            <div class="content">
              <div class="row">
                <label for="pointSize">Point Size</label>
                <input type="range" id="pointSize" min="1" max="20" value="4"/>
                <span id="sizeVal">4</span>
              </div>
              <div class="row">
                <label for="alpha">Transparency (0=transparent, 255=opaque)</label>
                <input type="range" id="alpha" min="0" max="255" value="180"/>
                <span id="alphaVal">180</span>
              </div>
              <div class="row">
                <label>Occlusion (danger!)</label>
                <input type="checkbox" id="occlusionChk"/>
                <button class="btn inline" id="applyOcclusionBtn">Apply Occlusion</button>
              </div>
              <div class="row">
                <label>Color Mode</label>
                <input type="radio" name="mode" id="modeColor" checked/>
                  <label for="modeColor">User Color</label>
                <input type="radio" name="mode" id="modeScan"/>
                  <label for="modeScan">Scan Color</label>

                <button class="btn inline" id="pickColorBtn" disabled>Pick</button>
              </div>
            </div>
            <script>
              const sizeSlider  = document.getElementById('pointSize');
              const alphaSlider = document.getElementById('alpha');
              const sizeVal     = document.getElementById('sizeVal');
              const alphaVal    = document.getElementById('alphaVal');
              const occChk      = document.getElementById('occlusionChk');
              const applyOccBtn = document.getElementById('applyOcclusionBtn');

              const modeColor   = document.getElementById('modeColor');
              const modeScan    = document.getElementById('modeScan');
              const pickColorBtn= document.getElementById('pickColorBtn');

              sizeSlider.addEventListener('input', ()=>{
                sizeVal.textContent = sizeSlider.value;
                updateSizeAlpha();
              });
              alphaSlider.addEventListener('input', ()=>{
                alphaVal.textContent = alphaSlider.value;
                updateSizeAlpha();
              });

              function updateSizeAlpha(){
                const ps = sizeSlider.value;
                const al = alphaSlider.value;
                sketchup.update_size_alpha(ps, al);
              }

              applyOccBtn.addEventListener('click', ()=>{
                const occ = occChk.checked;
                sketchup.update_occlusion(occ);
              });

              function refreshColorMode(){
                if(modeColor.checked){
                  // user color => enable pick button
                  pickColorBtn.disabled = false;
                } else {
                  // scan color => disable pick
                  pickColorBtn.disabled = true;
                }
                // Вызываем коллбек, чтобы overlay.use_e57_color был true/false
                sketchup.set_color_mode(modeScan.checked);
              }
              modeColor.addEventListener('change', refreshColorMode);
              modeScan.addEventListener('change', refreshColorMode);

              pickColorBtn.addEventListener('click', ()=>{
                sketchup.pick_color();
              });

              refreshColorMode();
            </script>
          </body>
          </html>
        HTML

        @@dialog.set_html(html)

        # (A) update_size_alpha
        @@dialog.add_action_callback("update_size_alpha") do |_ctx, sizeStr, alphaStr|
          overlay = GaussianPoints.overlay
          if overlay
            s = sizeStr.to_i
            a = alphaStr.to_i
            overlay.point_size = s
            overlay.visible_color.alpha = a
            overlay.hidden_color.alpha  = a
            Sketchup.active_model.active_view.invalidate
          end
        end

        # (B) update_occlusion
        @@dialog.add_action_callback("update_occlusion") do |_ctx, occStr|
          overlay = GaussianPoints.overlay
          if overlay
            overlay.occlusion_enabled = (occStr == true || occStr.to_s=="true")
            Sketchup.active_model.active_view.invalidate
          end
        end

        # (C) set_color_mode => bool => overlay.use_e57_color
        @@dialog.add_action_callback("set_color_mode") do |_ctx, scanStr|
          overlay = GaussianPoints.overlay
          if overlay
            # scanStr = "true"/"false"
            overlay.use_e57_color = (scanStr == true || scanStr.to_s=="true")
            Sketchup.active_model.active_view.invalidate
          end
        end

        # (D) pick_color => ColorDialog
        @@dialog.add_action_callback("pick_color") do |_ctx|
          ColorDialog.show_dialog
        end

        @@dialog.center
        @@dialog.show
      end

    end
  end
end
