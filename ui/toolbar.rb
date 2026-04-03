module GaussianPoints
  module UIparts
    module ToolbarManager
      def self.create_toolbar
        toolbar = UI::Toolbar.new('Gaussian_Points')

        cmd_import = UI::Command.new('Import') {
          GaussianPoints::IO::Importer.import_dialog
        }
        cmd_import.small_icon = File.join(GaussianPoints::PLUGIN_DIR, 'ui', 'icons', 'import_16.png')
        cmd_import.large_icon = File.join(GaussianPoints::PLUGIN_DIR, 'ui', 'icons', 'import_24.png')
        cmd_import.tooltip = 'Import'
        cmd_import.status_bar_text = 'Imports data from external files.'

        cmd_export = UI::Command.new('Export') {
          GaussianPoints::IO::Exporter.export_xyz
        }
        cmd_export.small_icon = File.join(GaussianPoints::PLUGIN_DIR, 'ui', 'icons', 'export_16.png')
        cmd_export.large_icon = File.join(GaussianPoints::PLUGIN_DIR, 'ui', 'icons', 'export_24.png')
        cmd_export.tooltip = 'Export'
        cmd_export.status_bar_text = 'Exports geometry as points.'

        cmd_gaus = UI::Command.new('Gaussian Splatting') {
          GaussianPoints::UIparts::GaussianSplattingDialog.show_dialog
        }
        cmd_gaus.small_icon = File.join(GaussianPoints::PLUGIN_DIR, 'ui', 'icons', 'gaus_16.png')
        cmd_gaus.large_icon = File.join(GaussianPoints::PLUGIN_DIR, 'ui', 'icons', 'gaus_24.png')
        cmd_gaus.tooltip = 'Gaussian Splatting'
        cmd_gaus.status_bar_text = 'Opens the integrated Gaussian splatting controls.'

        toolbar.add_item(cmd_import)
        toolbar.add_item(cmd_export)
        toolbar.add_item(cmd_gaus)
        toolbar.add_separator

        cmd_vis = UI::Command.new('Visualization') {
          VisualizationDialog.show_dialog
        }
        cmd_vis.small_icon = File.join(GaussianPoints::PLUGIN_DIR, 'ui', 'icons', 'vis_16.png')
        cmd_vis.large_icon = File.join(GaussianPoints::PLUGIN_DIR, 'ui', 'icons', 'vis_24.png')
        cmd_vis.tooltip = 'Visualization'
        cmd_vis.status_bar_text = 'Adjusts colors and transparency.'

        cmd_clip = UI::Command.new('Clipping Tool') {
          if ClippingManager.active?
            ClippingManager.disable_clip
            ClippingDialog.close_if_open
          else
            ClippingManager.enable_clip(activate_tool: false)
            ClippingDialog.close_if_open
          end
        }
        cmd_clip.small_icon = File.join(GaussianPoints::PLUGIN_DIR, 'ui', 'icons', 'clip_16.png')
        cmd_clip.large_icon = File.join(GaussianPoints::PLUGIN_DIR, 'ui', 'icons', 'clip_24.png')
        cmd_clip.set_validation_proc { ClippingManager.active? ? MF_CHECKED : MF_UNCHECKED }
        cmd_clip.tooltip = 'Clipping Tool'
        cmd_clip.status_bar_text = 'Enables or disables the clip box.'

        cmd_clip_move = UI::Command.new('Clip Move Gizmo') {
          ClippingManager.toggle_gizmo
          ClippingDialog.close_if_open
        }
        cmd_clip_move.small_icon = File.join(GaussianPoints::PLUGIN_DIR, 'ui', 'icons', 'clip_move_16.png')
        cmd_clip_move.large_icon = File.join(GaussianPoints::PLUGIN_DIR, 'ui', 'icons', 'clip_move_24.png')
        cmd_clip_move.set_validation_proc { ClippingManager.gizmo_enabled? ? MF_CHECKED : MF_UNCHECKED }
        cmd_clip_move.tooltip = 'Clip Gizmo'
        cmd_clip_move.status_bar_text = 'Toggles the clip gizmo for move and resize.'

        cmd_clip_hide = UI::Command.new('Clip Show/Hide') {
          ClippingManager.enable_clip(activate_tool: false) unless ClippingManager.active?
          ClippingManager.toggle_box_visibility
          ClippingDialog.close_if_open
        }
        cmd_clip_hide.small_icon = File.join(GaussianPoints::PLUGIN_DIR, 'ui', 'icons', 'clip_hide_16.png')
        cmd_clip_hide.large_icon = File.join(GaussianPoints::PLUGIN_DIR, 'ui', 'icons', 'clip_hide_24.png')
        cmd_clip_hide.tooltip = 'Show / Hide Clip Box'
        cmd_clip_hide.status_bar_text = 'Toggles clip box visibility while keeping clipping active.'

        cmd_clip_reset = UI::Command.new('Clip Reset') {
          ClippingManager.enable_clip(activate_tool: false) unless ClippingManager.active?
          ClippingManager.reset_box_position
          ClippingDialog.close_if_open
        }
        cmd_clip_reset.small_icon = File.join(GaussianPoints::PLUGIN_DIR, 'ui', 'icons', 'clip_reset_16.png')
        cmd_clip_reset.large_icon = File.join(GaussianPoints::PLUGIN_DIR, 'ui', 'icons', 'clip_reset_24.png')
        cmd_clip_reset.tooltip = 'Reset Clip Box'
        cmd_clip_reset.status_bar_text = 'Refits the clip box to the current scene bounds with padding.'

        cmd_clip_delete = UI::Command.new('Clip Remove') {
          ClippingManager.remove_clip
          ClippingDialog.close_if_open
        }
        cmd_clip_delete.small_icon = File.join(GaussianPoints::PLUGIN_DIR, 'ui', 'icons', 'clip_delete_16.png')
        cmd_clip_delete.large_icon = File.join(GaussianPoints::PLUGIN_DIR, 'ui', 'icons', 'clip_delete_24.png')
        cmd_clip_delete.tooltip = 'Remove Clip'
        cmd_clip_delete.status_bar_text = 'Disables clipping and removes the active clip box effect.'

        cmd_select = UI::Command.new('Select') {
          UI.messagebox('Select Tool Placeholder')
        }
        cmd_select.small_icon = File.join(GaussianPoints::PLUGIN_DIR, 'ui', 'icons', 'sel_16.png')
        cmd_select.large_icon = File.join(GaussianPoints::PLUGIN_DIR, 'ui', 'icons', 'sel_24.png')
        cmd_select.tooltip = 'Select'
        cmd_select.status_bar_text = 'Selects points.'

        toolbar.add_item(cmd_vis)
        toolbar.add_item(cmd_clip)
        toolbar.add_item(cmd_clip_move)
        toolbar.add_item(cmd_clip_hide)
        toolbar.add_item(cmd_clip_reset)
        toolbar.add_item(cmd_clip_delete)
        toolbar.add_item(cmd_select)
        toolbar.add_separator

        cmd_hide = UI::Command.new('Hide/Show') {
          overlay = GaussianPoints.overlay
          overlay.enabled = !overlay.enabled? if overlay
        }
        cmd_hide.small_icon = File.join(GaussianPoints::PLUGIN_DIR, 'ui', 'icons', 'eye_open_16.png')
        cmd_hide.large_icon = File.join(GaussianPoints::PLUGIN_DIR, 'ui', 'icons', 'eye_open_24.png')
        cmd_hide.set_validation_proc { GaussianPoints.overlay&.enabled? ? MF_UNCHECKED : MF_CHECKED }
        cmd_hide.tooltip = 'Hide/Show'
        cmd_hide.status_bar_text = 'Toggles overlay visibility.'

        cmd_delete = UI::Command.new('Delete') {
          DeleteDialog.show_dialog
        }
        cmd_delete.small_icon = File.join(GaussianPoints::PLUGIN_DIR, 'ui', 'icons', 'delete_16.png')
        cmd_delete.large_icon = File.join(GaussianPoints::PLUGIN_DIR, 'ui', 'icons', 'delete_24.png')
        cmd_delete.tooltip = 'Delete'
        cmd_delete.status_bar_text = 'Removes points from the model.'

        cmd_settings = UI::Command.new('Settings') {
          SettingsDialog.show_dialog
        }
        cmd_settings.small_icon = File.join(GaussianPoints::PLUGIN_DIR, 'ui', 'icons', 'settings_16.png')
        cmd_settings.large_icon = File.join(GaussianPoints::PLUGIN_DIR, 'ui', 'icons', 'settings_24.png')
        cmd_settings.tooltip = 'Settings'
        cmd_settings.status_bar_text = 'Configures optimization, language, and other preferences.'

        toolbar.add_item(cmd_hide)
        toolbar.add_item(cmd_delete)
        toolbar.add_item(cmd_settings)

        toolbar.show
        toolbar
      end
    end
  end
end
