# main.rb

module GaussianPoints
  PLUGIN_DIR = File.dirname(__FILE__) unless defined?(PLUGIN_DIR)

  @@overlay = nil
  @@toolbar = nil

  def self.overlay
    @@overlay
  end
  def self.overlay=(val); @@overlay = val; end

  def self.toolbar
    @@toolbar
  end
  def self.toolbar=(val); @@toolbar = val; end

  def self.init_plugin
    model = Sketchup.active_model

    # 1) Создаём Overlay
    ov = Overlays::PointOverlay.new
    model.overlays.add(ov)
    ov.enabled = true
    self.overlay = ov

    # 2) Запускаем тулбар
    tb = UIparts::ToolbarManager.create_toolbar
    self.toolbar = tb

    puts "[GaussianPoints] init_plugin: Overlay включён, Toolbar показан."
  end

  def self.stop_plugin
    # Удаляем Overlay
    if self.overlay
      Sketchup.active_model.overlays.remove(self.overlay) rescue nil
      self.overlay = nil
    end
    # Прячем Toolbar
    if self.toolbar
      self.toolbar.hide
      self.toolbar = nil
    end
    puts "[GaussianPoints] Плагин остановлен."
  end
end

# Подключаем остальные файлы
require File.join(GaussianPoints::PLUGIN_DIR, "overlays", "point_overlay.rb")

require File.join(GaussianPoints::PLUGIN_DIR, "io", "fiddle_pointcloud_hook")
# require File.join(GaussianPoints::PLUGIN_DIR, "io", "fiddle_pointcloud_renderer.rb")
require File.join(GaussianPoints::PLUGIN_DIR, "io", "fiddle_importer.rb")
require File.join(GaussianPoints::PLUGIN_DIR, "io", "fiddle_octree_processor.rb")
require File.join(GaussianPoints::PLUGIN_DIR, "io", "importer.rb")
require File.join(GaussianPoints::PLUGIN_DIR, "io", "exporter.rb")

require File.join(GaussianPoints::PLUGIN_DIR, "ui", "clipping_manager.rb")
require File.join(GaussianPoints::PLUGIN_DIR, "ui", "clipping_dialog.rb")
require File.join(GaussianPoints::PLUGIN_DIR, "ui", "visualization_dialog.rb")
require File.join(GaussianPoints::PLUGIN_DIR, "ui", "settings_dialog.rb")
require File.join(GaussianPoints::PLUGIN_DIR, "ui", "delete_dialog.rb")
require File.join(GaussianPoints::PLUGIN_DIR, "ui", "color_dialog.rb")

require File.join(GaussianPoints::PLUGIN_DIR, "ui", "toolbar.rb")

# Запуск
GaussianPoints.init_plugin
