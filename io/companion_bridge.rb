require 'json'
require 'fileutils'
require 'time'

module GaussianPoints
  module IO
    module CompanionBridge
      extend self

      APP_DATA_DIR = File.join(ENV['LOCALAPPDATA'].to_s, 'GaussianPointsCompanion').freeze
      LATEST_EXPORT_FILE = File.join(APP_DATA_DIR, 'latest_export.json').freeze
      BRIDGE_DIR = File.join(APP_DATA_DIR, 'bridge').freeze
      BRIDGE_STATUS_FILE = File.join(BRIDGE_DIR, 'status.json').freeze
      BRIDGE_COMMANDS_DIR = File.join(BRIDGE_DIR, 'commands').freeze
      BRIDGE_RESPONSES_DIR = File.join(BRIDGE_DIR, 'responses').freeze
      BRIDGE_SESSIONS_DIR = File.join(BRIDGE_DIR, 'sessions').freeze
      BRIDGE_PREVIEWS_DIR = File.join(BRIDGE_DIR, 'previews').freeze
      BRIDGE_LOG_FILE = File.join(BRIDGE_DIR, 'bridge.log').freeze

      def launch
        start_bridge_timer
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

        start_bridge_timer
        menu = UI.menu('Plugins').add_submenu('Gaussian Points')
        menu.add_item('Enable Gaussian Points') { GaussianPoints.init_plugin }
        menu.add_item('Disable Gaussian Points') { GaussianPoints.stop_plugin }
        menu.add_separator
        menu.add_item('Open Gaussian Splat Trainer') { launch }
        menu.add_item('Import Latest Companion Result') do
          GaussianPoints.init_plugin
          import_latest_result
        end
        menu.add_separator
        menu.add_item('Open Gaussian Splat Loader') do
          GaussianPoints.init_plugin
          GaussianPoints::UIparts::GaussianSplattingDialog.show_dialog
        end
        @menu_registered = true
      end

      def start_bridge_timer
        return if @bridge_timer_started

        ensure_bridge_dirs
        log_bridge('start_bridge_timer')
        write_bridge_status
        @bridge_timer_started = true
        @bridge_timer = UI.start_timer(1.0, true) do
          bridge_tick
        end
      rescue StandardError => e
        puts "[GaussianPoints] bridge timer error: #{e.class}: #{e.message}"
      end

      private

      def bridge_tick
        write_bridge_status
        process_pending_commands
      rescue StandardError => e
        log_bridge("bridge_tick exception #{e.class}: #{e.message}")
        puts "[GaussianPoints] bridge tick error: #{e.class}: #{e.message}"
      end

      def process_pending_commands
        ensure_bridge_dirs
        command_dir = session_commands_dir
        FileUtils.mkdir_p(command_dir)
        command_files = Dir.children(command_dir)
          .select { |entry| entry.downcase.end_with?('.json') }
          .map { |entry| File.join(command_dir, entry) }
          .select { |path| File.file?(path) }
          .sort
        command_files.each do |command_file|
          handle_command_file(command_file)
        end
      end

      def handle_command_file(command_file)
        command_id = nil
        response_path = nil
        payload = JSON.parse(File.read(command_file))
        command_id = payload['id'].to_s
        command_id = File.basename(command_file, '.json') if command_id.empty?
        response_path = File.join(BRIDGE_RESPONSES_DIR, "#{command_id}.json")
        log_bridge("handle_command_file id=#{command_id} command=#{payload['command']}")

        case payload['command'].to_s
        when 'import_scene'
          scene_path = payload['scene_path'].to_s
          scene_name = payload['scene_name'].to_s
          unless File.exist?(scene_path)
            write_response(response_path, command_id, ok: false, error: "Scene file was not found:\n#{scene_path}")
            log_bridge("import_scene missing id=#{command_id} path=#{scene_path}")
            return
          end
          unless GaussianPoints.init_plugin
            write_response(response_path, command_id, ok: false, error: 'Gaussian Points plugin failed to initialize in SketchUp.')
            log_bridge("import_scene init_plugin failed id=#{command_id}")
            return
          end

          write_response(
            response_path,
            command_id,
            ok: true,
            message: "SketchUp accepted #{File.basename(scene_path)} for import."
          )
          log_bridge("import_scene accepted id=#{command_id} path=#{scene_path}")
          UI.start_timer(0, false) do
            begin
              imported = GaussianPoints::IO::GaussianSceneImporter.load_file(
                scene_path,
                mode: 'direct',
                name: scene_name.empty? ? File.basename(scene_path) : scene_name
              )
              if imported.nil?
                log_bridge("import_scene load_file returned nil id=#{command_id} path=#{scene_path}")
                UI.messagebox("SketchUp could not import:\n#{scene_path}")
              else
                log_bridge("import_scene completed id=#{command_id} path=#{scene_path}")
              end
            rescue StandardError => e
              log_bridge("import_scene exception id=#{command_id} #{e.class}: #{e.message}")
              UI.messagebox("SketchUp import failed:\n#{e.class}: #{e.message}")
            end
          end
        else
          write_response(response_path, command_id, ok: false, error: "Unsupported bridge command: #{payload['command']}")
          log_bridge("unsupported command id=#{command_id} command=#{payload['command']}")
        end
      rescue StandardError => e
        fallback_id = command_id || File.basename(command_file, '.json')
        response_path ||= File.join(BRIDGE_RESPONSES_DIR, "#{fallback_id}.json")
        log_bridge("handle_command_file exception id=#{fallback_id} #{e.class}: #{e.message}")
        write_response(
          response_path,
          fallback_id,
          ok: false,
          error: "#{e.class}: #{e.message}"
        )
      ensure
        File.delete(command_file) if command_file && File.exist?(command_file)
      end

      def write_response(path, command_id, ok:, message: nil, error: nil)
        ensure_bridge_dirs
        payload = {
          id: command_id,
          ok: ok ? true : false,
          message: message,
          error: error,
          completed_at: Time.now.utc.iso8601
        }
        atomic_write_json(path, payload)
        log_bridge("write_response id=#{command_id} ok=#{ok ? true : false}")
      end

      def write_bridge_status
        ensure_bridge_dirs
        preview_path = current_session_preview_path
        write_session_preview(preview_path)
        payload = {
          id: current_session_id,
          updated_at: Time.now.utc.iso8601,
          sketchup_pid: Process.pid,
          plugin_enabled: GaussianPoints.plugin_enabled? ? true : false,
          plugin_dir: GaussianPoints::PLUGIN_DIR,
          model_name: current_model_name,
          model_path: current_model_path,
          description: current_model_description,
          preview_path: File.exist?(preview_path) ? preview_path : ''
        }
        atomic_write_json(current_session_status_path, payload)
        atomic_write_json(BRIDGE_STATUS_FILE, payload)
      end

      def ensure_bridge_dirs
        FileUtils.mkdir_p(APP_DATA_DIR)
        FileUtils.mkdir_p(BRIDGE_COMMANDS_DIR)
        FileUtils.mkdir_p(BRIDGE_RESPONSES_DIR)
        FileUtils.mkdir_p(BRIDGE_SESSIONS_DIR)
        FileUtils.mkdir_p(BRIDGE_PREVIEWS_DIR)
        FileUtils.mkdir_p(session_commands_dir)
      end

      def atomic_write_json(path, payload)
        FileUtils.mkdir_p(File.dirname(path))
        File.write(path, JSON.pretty_generate(payload))
      end

      def log_bridge(message)
        ensure_bridge_dirs
        File.open(BRIDGE_LOG_FILE, 'a') do |file|
          file.puts("[#{Time.now.utc.iso8601}] #{message}")
        end
      rescue StandardError
        nil
      end

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

      def current_session_id
        Process.pid.to_s
      end

      def current_session_status_path
        File.join(BRIDGE_SESSIONS_DIR, "#{current_session_id}.json")
      end

      def current_session_preview_path
        File.join(BRIDGE_PREVIEWS_DIR, "#{current_session_id}.png")
      end

      def session_commands_dir
        File.join(BRIDGE_COMMANDS_DIR, current_session_id)
      end

      def current_model
        Sketchup.active_model
      rescue StandardError
        nil
      end

      def current_model_path
        model = current_model
        return '' unless model

        model.path.to_s
      rescue StandardError
        ''
      end

      def current_model_name
        model = current_model
        return 'Untitled.skp' unless model

        path = model.path.to_s
        return File.basename(path) unless path.empty?

        title = model.title.to_s.strip
        return 'Untitled.skp' if title.empty?

        title.downcase.end_with?('.skp') ? title : "#{title}.skp"
      rescue StandardError
        'Untitled.skp'
      end

      def current_model_description
        path = current_model_path
        path.empty? ? 'Unsaved • SketchUp' : 'Open Project • SketchUp'
      end

      def write_session_preview(preview_path)
        model = current_model
        return unless model

        view = model.active_view
        return unless view

        now = Time.now
        model_key = [current_model_name, current_model_path].join('|')
        return if @last_preview_model_key == model_key &&
                  @last_preview_written_at &&
                  (now - @last_preview_written_at) < 3.0 &&
                  File.exist?(preview_path)

        ok = view.write_image(
          filename: preview_path,
          width: 320,
          height: 180,
          antialias: true,
          compression: 0.9
        )
        if ok
          @last_preview_model_key = model_key
          @last_preview_written_at = now
        end
      rescue StandardError => e
        log_bridge("write_session_preview exception #{e.class}: #{e.message}")
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
