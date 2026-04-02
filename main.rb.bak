module GaussianPoints
  module UIparts
    module ToolbarManager
      def self.create_toolbar
        toolbar = UI::Toolbar.new("Gaussian_Points")

        # 1) Import
        cmd_import = UI::Command.new("Import") {
          GaussianPoints::IO::Importer.import_dialog
        }
        cmd_import.small_icon = File.join(GaussianPoints::PLUGIN_DIR, "ui", "icons", "import_16.png")
        cmd_import.large_icon = File.join(GaussianPoints::PLUGIN_DIR, "ui", "icons", "import_24.png")
        cmd_import.tooltip = "Import"
        cmd_import.status_bar_text = "Imports data from external files."

        # 2) Export
        cmd_export = UI::Command.new("Export") {
          GaussianPoints::IO::Exporter.export_xyz
        }
        cmd_export.small_icon = File.join(GaussianPoints::PLUGIN_DIR, "ui", "icons", "export_16.png")
        cmd_export.large_icon = File.join(GaussianPoints::PLUGIN_DIR, "ui", "icons", "export_24.png")
        cmd_export.tooltip = "Export"
        cmd_export.status_bar_text = "Exports geometry as points."

        # 3) Gaussian Splatting
        cmd_gaus = UI::Command.new("Gaussian Splatting") {
          UI.messagebox("Gaussian Splatting Placeholder")
        }
        cmd_gaus.small_icon = File.join(GaussianPoints::PLUGIN_DIR, "ui", "icons", "gaus_16.png")
        cmd_gaus.large_icon = File.join(GaussianPoints::PLUGIN_DIR, "ui", "icons", "gaus_24.png")
        cmd_gaus.tooltip = "Gaussian Splatting"
        cmd_gaus.status_bar_text = "Manages Gaussian splatting effects."

        # Добавляем разделитель
        toolbar.add_item(cmd_import)
        toolbar.add_item(cmd_export)
        toolbar.add_item(cmd_gaus)
        toolbar.add_separator

        # 4) Visualization
        cmd_vis = UI::Command.new("Visualization") {
          VisualizationDialog.show_dialog
        }
        cmd_vis.small_icon = File.join(GaussianPoints::PLUGIN_DIR, "ui", "icons", "vis_16.png")
        cmd_vis.large_icon = File.join(GaussianPoints::PLUGIN_DIR, "ui", "icons", "vis_24.png")
        cmd_vis.tooltip = "Visualization"
        cmd_vis.status_bar_text = "Adjusts colors and transparency."

        # 5) Clipping Tool
        cmd_clip = UI::Command.new("Clipping Tool") {
          ClippingManager.toggle_clip
          if ClippingManager.active?
            ClippingDialog.show_dialog
          else
            ClippingDialog.close_if_open
          end
        }
        cmd_clip.small_icon = File.join(GaussianPoints::PLUGIN_DIR, "ui", "icons", "clip_16.png")
        cmd_clip.large_icon = File.join(GaussianPoints::PLUGIN_DIR, "ui", "icons", "clip_24.png")
        cmd_clip.set_validation_proc { ClippingManager.active? ? MF_CHECKED : MF_UNCHECKED }
        cmd_clip.tooltip = "Clipping Tool"
        cmd_clip.status_bar_text = "Clips geometry outside a defined box."

        # 6) Move Tool
        cmd_move = UI::Command.new("Move Tool") {
          UI.messagebox("Move Tool Placeholder")
        }
        cmd_move.small_icon = File.join(GaussianPoints::PLUGIN_DIR, "ui", "icons", "move_16.png")
        cmd_move.large_icon = File.join(GaussianPoints::PLUGIN_DIR, "ui", "icons", "move_24.png")
        cmd_move.tooltip = "Move Tool"
        cmd_move.status_bar_text = "Moves objects."

        # 7) Select
        cmd_select = UI::Command.new("Select") {
          UI.messagebox("Select Tool Placeholder")
        }
        cmd_select.small_icon = File.join(GaussianPoints::PLUGIN_DIR, "ui", "icons", "sel_16.png")
        cmd_select.large_icon = File.join(GaussianPoints::PLUGIN_DIR, "ui", "icons", "sel_24.png")
        cmd_select.tooltip = "Select"
        cmd_select.status_bar_text = "Selects points."

        toolbar.add_item(cmd_vis)
        toolbar.add_item(cmd_clip)
        toolbar.add_item(cmd_move)
        toolbar.add_item(cmd_select)
        toolbar.add_separator

        # 8) Hide/Show
        cmd_hide = UI::Command.new("Hide/Show") {
          overlay = GaussianPoints.overlay
          if overlay
            overlay.enabled = !overlay.enabled?
          end
        }
        cmd_hide.small_icon = File.join(GaussianPoints::PLUGIN_DIR, "ui", "icons", "eye_open_16.png")
        cmd_hide.large_icon = File.join(GaussianPoints::PLUGIN_DIR, "ui", "icons", "eye_open_24.png")
        cmd_hide.set_validation_proc { GaussianPoints.overlay&.enabled? ? MF_UNCHECKED : MF_CHECKED }
        cmd_hide.tooltip = "Hide/Show"
        cmd_hide.status_bar_text = "Toggles overlay visibility."

        # 9) Delete
        cmd_delete = UI::Command.new("Delete") {
          DeleteDialog.show_dialog
        }
        cmd_delete.small_icon = File.join(GaussianPoints::PLUGIN_DIR, "ui", "icons", "delete_16.png")
        cmd_delete.large_icon = File.join(GaussianPoints::PLUGIN_DIR, "ui", "icons", "delete_24.png")
        cmd_delete.tooltip = "Delete"
        cmd_delete.status_bar_text = "Removes points from the model."

        # 10) Settings
        cmd_settings = UI::Command.new("Settings") {
          SettingsDialog.show_dialog
        }
        cmd_settings.small_icon = File.join(GaussianPoints::PLUGIN_DIR, "ui", "icons", "settings_16.png")
        cmd_settings.large_icon = File.join(GaussianPoints::PLUGIN_DIR, "ui", "icons", "settings_24.png")
        cmd_settings.tooltip = "Settings"
        cmd_settings.status_bar_text = "Configures optimization, language, and other preferences."

        toolbar.add_item(cmd_hide)
        toolbar.add_item(cmd_delete)
        toolbar.add_item(cmd_settings)

        toolbar.show
        toolbar
      end
    end
  end
end
