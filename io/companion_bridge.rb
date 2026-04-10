require 'json'

module GaussianPoints
  module IO
    module CompanionBridge
      extend self

      APP_DATA_DIR = File.join(ENV['LOCALAPPDATA'].to_s, 'GaussianPointsCompanion').freeze
      LATEST_EXPORT_FILE = File.join(APP_DATA_DIR, 'latest_export.json').freeze

      def launch
        python = find_python_gui
        return UI.messagebox('Python 3 with tkinter was not found. Install Python and try again.') unless python

        pid = Process.spawn(
          python,
          '-m',
          'companion_app',
          "--plugin-root=#{GaussianPoints::PLUGIN_DIR}",
          chdir: GaussianPoints::PLUGIN_DIR
        )
        Process.detach(pid)
        true
      rescue StandardError => e
        UI.messagebox("Failed to launch companion app:\n#{e.message}")
        false
      end

      def import_latest_result
        payload = latest_export_payload
        scene_path = payload && preferred_scene_path(payload)
        unless scene_path && File.exist?(scene_path)
          UI.messagebox("No companion export was found.\nRun an export from the desktop app first.")
          return nil
        end

        GaussianPoints::IO::GaussianSceneImporter.load_with_mode_prompt(
          scene_path,
          name: File.basename(scene_path)
        )
      rescue StandardError => e
        UI.messagebox("Failed to import companion result:\n#{e.message}")
        nil
      end

      def register_menu
        return if @menu_registered

        menu = UI.menu('Plugins').add_submenu('Gaussian Points')
        menu.add_item('Open Gaussian Splat Trainer') { launch }
        menu.add_item('Import Latest Companion Result') { import_latest_result }
        menu.add_separator
        menu.add_item('Open Gaussian Splat Loader') do
          GaussianPoints::UIparts::GaussianSplattingDialog.show_dialog
        end
        @menu_registered = true
      end

      private

      def latest_export_payload
        return nil unless File.exist?(LATEST_EXPORT_FILE)

        JSON.parse(File.read(LATEST_EXPORT_FILE))
      rescue StandardError
        nil
      end

      def preferred_scene_path(payload)
        candidates = [payload['scene_gasp'], payload['scene_ply']]
        candidates.find { |path| path && File.exist?(path) }
      end

      def find_python_gui
        candidates = []
        candidates << ENV['PYTHONW'] if ENV['PYTHONW']
        # Prefer the bundled venvs that have PySide6 and other dependencies
        %w[.gstrain310 .gstrain311].each do |venv|
          candidates << File.join(GaussianPoints::PLUGIN_DIR, venv, 'Scripts', 'pythonw.exe')
          candidates << File.join(GaussianPoints::PLUGIN_DIR, venv, 'Scripts', 'python.exe')
        end
        if ENV['LOCALAPPDATA']
          candidates.concat(Dir.glob(File.join(ENV['LOCALAPPDATA'], 'Programs', 'Python', 'Python*', 'pythonw.exe')))
          candidates.concat(Dir.glob(File.join(ENV['LOCALAPPDATA'], 'Programs', 'Python', 'Python*', 'python.exe')))
        end
        candidates.concat(%w[pythonw.exe python.exe])
        candidates.find do |candidate|
          candidate && !candidate.empty? && (candidate.include?(File::SEPARATOR) || candidate.include?('\\') ? File.exist?(candidate) : true)
        end
      end
    end
  end
end
