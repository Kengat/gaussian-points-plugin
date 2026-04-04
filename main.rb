# main.rb

module GaussianPoints
  PLUGIN_DIR = File.dirname(__FILE__) unless defined?(PLUGIN_DIR)

  @@overlay = nil
  @@toolbar = nil

  def self.overlay
    @@overlay
  end

  def self.overlay=(val)
    @@overlay = val
  end

  def self.toolbar
    @@toolbar
  end

  def self.toolbar=(val)
    @@toolbar = val
  end

  def self.init_plugin
    return if self.overlay && self.toolbar

    UIparts::RenderItemRegistry.clear_all if defined?(UIparts::RenderItemRegistry)

    model = Sketchup.active_model
    UIparts::RenderItemRegistry.ensure_model_observer!(model) if defined?(UIparts::RenderItemRegistry)

    overlay_instance = Overlays::PointOverlay.new
    model.overlays.add(overlay_instance)
    overlay_instance.enabled = true
    self.overlay = overlay_instance

    toolbar_instance = UIparts::ToolbarManager.create_toolbar
    self.toolbar = toolbar_instance

    puts '[GaussianPoints] init_plugin: overlay enabled, toolbar shown.'
  end

  def self.stop_plugin
    UIparts::RenderItemRegistry.clear_all if defined?(UIparts::RenderItemRegistry)
    UIparts::RenderItemRegistry.detach_model_observer! if defined?(UIparts::RenderItemRegistry)

    if self.overlay
      Sketchup.active_model.overlays.remove(self.overlay) rescue nil
      self.overlay = nil
    end

    if self.toolbar
      self.toolbar.hide
      self.toolbar = nil
    end

    puts '[GaussianPoints] plugin stopped.'
  end
end

require File.join(GaussianPoints::PLUGIN_DIR, 'overlays', 'point_overlay.rb')
require File.join(GaussianPoints::PLUGIN_DIR, 'scene_bounds_proxy.rb')

require File.join(GaussianPoints::PLUGIN_DIR, 'io', 'native_paths.rb')
require File.join(GaussianPoints::PLUGIN_DIR, 'io', 'overlay_bridge_native.rb')
require File.join(GaussianPoints::PLUGIN_DIR, 'io', 'pointcloud_hook_native.rb')
require File.join(GaussianPoints::PLUGIN_DIR, 'io', 'e57_importer_native.rb')
require File.join(GaussianPoints::PLUGIN_DIR, 'io', 'octree_processor_native.rb')
require File.join(GaussianPoints::PLUGIN_DIR, 'io', 'gasp_project.rb')
require File.join(GaussianPoints::PLUGIN_DIR, 'io', 'importer.rb')
require File.join(GaussianPoints::PLUGIN_DIR, 'io', 'exporter.rb')

require File.join(GaussianPoints::PLUGIN_DIR, 'ui', 'clipping_box_manager.rb')
require File.join(GaussianPoints::PLUGIN_DIR, 'ui', 'oriented_box_gizmo_tool.rb')
require File.join(GaussianPoints::PLUGIN_DIR, 'ui', 'clipping_box_tool.rb')
require File.join(GaussianPoints::PLUGIN_DIR, 'ui', 'render_item_registry.rb')
require File.join(GaussianPoints::PLUGIN_DIR, 'ui', 'move_tool_manager.rb')
require File.join(GaussianPoints::PLUGIN_DIR, 'ui', 'move_tool.rb')
require File.join(GaussianPoints::PLUGIN_DIR, 'ui', 'import_progress_dialog.rb')
require File.join(GaussianPoints::PLUGIN_DIR, 'ui', 'clipping_dialog.rb')
require File.join(GaussianPoints::PLUGIN_DIR, 'ui', 'visualization_dialog.rb')
require File.join(GaussianPoints::PLUGIN_DIR, 'ui', 'gaussian_splatting_dialog.rb')
require File.join(GaussianPoints::PLUGIN_DIR, 'ui', 'settings_dialog.rb')
require File.join(GaussianPoints::PLUGIN_DIR, 'ui', 'delete_dialog.rb')
require File.join(GaussianPoints::PLUGIN_DIR, 'ui', 'color_dialog.rb')

require File.join(GaussianPoints::PLUGIN_DIR, 'sandbox', 'gaussian_splats_sandbox.rb')

require File.join(GaussianPoints::PLUGIN_DIR, 'ui', 'toolbar.rb')

GaussianPoints.init_plugin
