# main.rb
require 'sketchup.rb'

module GaussianPoints
  PLUGIN_DIR = File.dirname(__FILE__) unless defined?(PLUGIN_DIR)

  @@overlay = nil
  @@toolbar = nil
  @@app_observer = nil
  @@plugin_enabled = false
  @@runtime_bootstrapped = false

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

  def self.plugin_enabled?
    @@plugin_enabled == true
  end

  def self.runtime_bootstrapped?
    @@runtime_bootstrapped == true
  end

  def self.bootstrap_runtime
    return true if runtime_bootstrapped?

    require File.join(PLUGIN_DIR, 'overlays', 'point_overlay.rb')
    require File.join(PLUGIN_DIR, 'scene_bounds_proxy.rb')

    require File.join(PLUGIN_DIR, 'io', 'native_paths.rb')
    require File.join(PLUGIN_DIR, 'io', 'overlay_bridge_native.rb')
    require File.join(PLUGIN_DIR, 'io', 'pointcloud_hook_native.rb')
    require File.join(PLUGIN_DIR, 'io', 'e57_importer_native.rb')
    require File.join(PLUGIN_DIR, 'io', 'octree_processor_native.rb')
    require File.join(PLUGIN_DIR, 'io', 'gasp_project.rb')
    require File.join(PLUGIN_DIR, 'io', 'importer.rb')
    require File.join(PLUGIN_DIR, 'io', 'exporter.rb')

    require File.join(PLUGIN_DIR, 'ui', 'clipping_box_manager.rb')
    require File.join(PLUGIN_DIR, 'ui', 'oriented_box_gizmo_tool.rb')
    require File.join(PLUGIN_DIR, 'ui', 'clipping_box_tool.rb')
    require File.join(PLUGIN_DIR, 'ui', 'render_item_registry.rb')
    require File.join(PLUGIN_DIR, 'ui', 'move_tool_manager.rb')
    require File.join(PLUGIN_DIR, 'ui', 'move_tool.rb')
    require File.join(PLUGIN_DIR, 'ui', 'import_progress_dialog.rb')
    require File.join(PLUGIN_DIR, 'ui', 'gaussian_splat_load_dialog.rb')
    require File.join(PLUGIN_DIR, 'io', 'gaussian_scene_importer.rb')
    require File.join(PLUGIN_DIR, 'ui', 'clipping_dialog.rb')
    require File.join(PLUGIN_DIR, 'ui', 'visualization_dialog.rb')
    require File.join(PLUGIN_DIR, 'ui', 'gaussian_splatting_dialog.rb')
    require File.join(PLUGIN_DIR, 'ui', 'settings_dialog.rb')
    require File.join(PLUGIN_DIR, 'ui', 'delete_dialog.rb')
    require File.join(PLUGIN_DIR, 'ui', 'color_dialog.rb')

    require File.join(PLUGIN_DIR, 'sandbox', 'gaussian_splats_sandbox.rb')
    require File.join(PLUGIN_DIR, 'ui', 'toolbar.rb')

    @@runtime_bootstrapped = true
    true
  rescue StandardError => e
    puts "[GaussianPoints] bootstrap_runtime error: #{e.class}: #{e.message}"
    false
  end

  def self.attach_to_model(model = Sketchup.active_model)
    return unless model

    UIparts::RenderItemRegistry.clear_all if defined?(UIparts::RenderItemRegistry)
    UIparts::RenderItemRegistry.detach_model_observer! if defined?(UIparts::RenderItemRegistry)
    UIparts::RenderItemRegistry.ensure_model_observer!(model) if defined?(UIparts::RenderItemRegistry)

    overlay_instance = Overlays::PointOverlay.new
    model.overlays.add(overlay_instance)
    overlay_instance.enabled = true
    self.overlay = overlay_instance
  rescue StandardError => e
    puts "[GaussianPoints] attach_to_model error: #{e.class}: #{e.message}"
  end

  def self.init_plugin
    return true if plugin_enabled? && self.overlay && self.toolbar
    return false unless bootstrap_runtime

    UIparts::RenderItemRegistry.clear_all if defined?(UIparts::RenderItemRegistry)

    attach_to_model(Sketchup.active_model)

    unless self.toolbar
      toolbar_instance = UIparts::ToolbarManager.create_toolbar
      self.toolbar = toolbar_instance
    end
    @@plugin_enabled = true

    puts '[GaussianPoints] init_plugin: overlay enabled, toolbar shown.'
    true
  rescue StandardError => e
    puts "[GaussianPoints] init_plugin error: #{e.class}: #{e.message}"
    false
  end

  def self.handle_model_switched(model = Sketchup.active_model)
    return unless plugin_enabled?

    GaussianPoints::GaussianSplats.clear_splats if defined?(GaussianPoints::GaussianSplats)
    GaussianPoints::SceneBoundsProxy.clear_splats if defined?(GaussianPoints::SceneBoundsProxy)
    self.overlay = nil
    attach_to_model(model)
    puts '[GaussianPoints] model switch handled.'
  rescue StandardError => e
    puts "[GaussianPoints] handle_model_switched error: #{e.class}: #{e.message}"
  end

  def self.stop_plugin
    return true unless plugin_enabled?

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
    GaussianPoints::GaussianSplats.clear_splats if defined?(GaussianPoints::GaussianSplats)
    GaussianPoints::SceneBoundsProxy.clear_splats if defined?(GaussianPoints::SceneBoundsProxy)
    @@plugin_enabled = false

    puts '[GaussianPoints] plugin stopped.'
    true
  rescue StandardError => e
    puts "[GaussianPoints] stop_plugin error: #{e.class}: #{e.message}"
    false
  end

  class AppLifecycleObserver < Sketchup::AppObserver
    def onNewModel(model)
      GaussianPoints.handle_model_switched(model)
    end

    def onOpenModel(model)
      GaussianPoints.handle_model_switched(model)
    end

    def onActivateModel(model)
      GaussianPoints.handle_model_switched(model)
    end
  end

  def self.install_app_observer
    return if @@app_observer

    @@app_observer = AppLifecycleObserver.new
    Sketchup.add_observer(@@app_observer)
  rescue StandardError => e
    puts "[GaussianPoints] install_app_observer error: #{e.class}: #{e.message}"
  end
end

require File.join(GaussianPoints::PLUGIN_DIR, 'io', 'companion_bridge.rb')

GaussianPoints.install_app_observer
GaussianPoints::IO::CompanionBridge.register_menu
UI.start_timer(0, false) { GaussianPoints.init_plugin }
