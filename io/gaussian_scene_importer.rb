module GaussianPoints
  module IO
    module GaussianSceneImporter
      extend self

      SUPPORTED_SCENE_FILTER = 'Gaussian Scenes|*.gasp;*.ply|Gaussian GASP|*.gasp|Gaussian PLY|*.ply||'.freeze

      def load_dialog
        filename = UI.openpanel('Choose a Gaussian scene file', '', SUPPORTED_SCENE_FILTER)
        return unless filename

        load_with_mode_prompt(filename)
      end

      def load_with_mode_prompt(filename, name: nil)
        return load_file(filename, mode: 'direct', name: name) if File.extname(filename.to_s).downcase == '.gasp'

        GaussianPoints::UIparts::GaussianSplatLoadDialog.choose(filename) do |mode|
          load_file(filename, mode: mode, name: name)
        end
      end

      def load_file(filename, mode: 'direct', name: nil)
        target = mode.to_s == 'gasp' ? ensure_gasp_for(filename) : filename
        return nil unless target

        GaussianPoints::UIparts::RenderItemRegistry.register_gaussian_file(
          name: name || File.basename(target),
          filename: target
        )
      rescue StandardError => e
        UI.messagebox("Failed to load Gaussian scene:\n#{e.message}")
        nil
      end

      private

      def ensure_gasp_for(filename)
        return filename if File.extname(filename.to_s).downcase == '.gasp'

        destination = File.join(File.dirname(filename), "#{File.basename(filename, '.*')}.gasp")
        return destination if File.exist?(destination) && File.mtime(destination) >= File.mtime(filename)

        python = find_python
        unless python
          UI.messagebox('Python with the companion app environment was not found. Load directly or install the training environment.')
          return nil
        end

        code = [
          'import sys',
          'from pathlib import Path',
          'sys.path.insert(0, sys.argv[1])',
          'from companion_app.gaussian_gasp import write_gaussian_gasp_from_ply',
          'write_gaussian_gasp_from_ply(sys.argv[2], sys.argv[3])'
        ].join('; ')
        ok = system(python, '-c', code, GaussianPoints::PLUGIN_DIR, filename, destination)
        unless ok && File.exist?(destination)
          UI.messagebox("Failed to create GASP cache:\n#{destination}")
          return nil
        end
        destination
      end

      def find_python
        candidates = []
        %w[.gstrain310 .gstrain311].each do |venv|
          candidates << File.join(GaussianPoints::PLUGIN_DIR, venv, 'Scripts', 'python.exe')
          candidates << File.join(GaussianPoints::PLUGIN_DIR, venv, 'Scripts', 'pythonw.exe')
        end
        candidates << ENV['PYTHON'] if ENV['PYTHON']
        candidates.concat(%w[python.exe python])
        candidates.compact.find { |path| executable_available?(path) }
      end

      def executable_available?(path)
        return File.exist?(path) if path.include?('/') || path.include?('\\') || path =~ /\A[A-Za-z]:/

        ENV['PATH'].to_s.split(File::PATH_SEPARATOR).any? do |dir|
          File.exist?(File.join(dir, path))
        end
      end
    end
  end
end
