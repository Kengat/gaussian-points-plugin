module GaussianPoints
  module IO
    module Importer
      extend self

      SUPPORTED_IMPORT_FILTERS = 'Supported Files|*.gasp;*.xyz;*.e57||GASP|*.gasp||XYZ|*.xyz||E57|*.e57||All|*.*||'.freeze
      E57_STATE_IDLE = 0
      E57_STATE_RUNNING = 1
      E57_STATE_COMPLETED = 2
      E57_STATE_FAILED = 3
      XYZ_READ_LINES_PER_TICK = 20_000
      XYZ_INSPECT_LINES_PER_TICK = 50_000
      E57_FETCH_POINTS_PER_TICK = 20_000
      CENTER_POINTS_PER_TICK = 40_000

      def import_dialog
        filename = UI.openpanel('Select file', '', SUPPORTED_IMPORT_FILTERS)
        return unless filename

        ext = File.extname(filename).downcase
        case ext
        when '.gasp'
          import_gasp(filename)
        when '.xyz', '.e57'
          prepare_guided_import(filename)
        else
          UI.messagebox("Unknown file format: #{ext}")
        end
      end

      def import_xyz(filename = nil)
        filename ||= UI.openpanel('Select .xyz', '', 'XYZ|*.xyz||')
        return unless filename

        prepare_guided_import(filename)
      end

      def import_e57(filename = nil)
        filename ||= UI.openpanel('Select .e57', '', 'E57|*.e57||')
        return unless filename

        prepare_guided_import(filename)
      end

      def on_import_dialog_ready
        if @active_import
          GaussianPoints::UIparts::ImportProgressDialog.update(dialog_payload_for_active_session)
        elsif @pending_import
          GaussianPoints::UIparts::ImportProgressDialog.update(dialog_payload_for_pending_import)
        end
      end

      def begin_pending_import(mode)
        pending = @pending_import
        return unless pending
        return unless pending[:ready]

        create_gasp = (mode.to_s == 'gasp')
        @pending_import = nil
        @active_import = {
          filename: pending[:filename],
          ext: pending[:ext],
          point_count: pending[:point_count],
          display_name: File.basename(pending[:filename]),
          base_name: File.basename(pending[:filename], '.*'),
          create_gasp: create_gasp,
          target_mode_label: create_gasp ? 'Create and load .gasp' : 'Load directly',
          phase: nil,
          progress_percent: 0,
          detail: nil,
          flat_data: [],
          min_x: nil,
          min_y: nil,
          min_z: nil,
          max_x: nil,
          max_y: nil,
          max_z: nil
        }

        case pending[:ext]
        when '.e57'
          unless GaussianPoints::IO::E57FiddleImporter.start_async_import(pending[:filename])
            fail_active_import('Failed to start native E57 import.')
            return
          end
          @active_import[:phase] = :e57_native_import
          @active_import[:status] = 'Reading E57 source'
          @active_import[:detail] = 'Importing points in a background worker.'
        when '.xyz'
          file = File.open(pending[:filename], 'r')
          @active_import[:phase] = :xyz_read
          @active_import[:xyz_file] = file
          @active_import[:xyz_file_size] = File.size(pending[:filename]).to_f
          @active_import[:xyz_color_scale] = pending[:xyz_color_scale] || 1.0
          @active_import[:status] = 'Reading XYZ source'
          @active_import[:detail] = 'Streaming points from the file.'
        else
          fail_active_import('Unsupported guided import file type.')
          return
        end

        GaussianPoints::UIparts::ImportProgressDialog.show(dialog_payload_for_active_session.merge(show_choices: false))
        schedule_next_tick
      rescue StandardError => e
        fail_active_import("Import setup failed: #{e.message}")
      end

      def cancel_active_import
        cleanup_pending_import
        @active_import = nil
        GaussianPoints::UIparts::ImportProgressDialog.close
      end

      def import_gasp(filename = nil, display_name: nil, source_path: nil)
        unless filename
          filename = UI.openpanel('Select .gasp', '', 'GASP|*.gasp||')
          return unless filename
        end
        unless defined?(GaussianPoints::Hook) &&
               GaussianPoints::Hook.respond_to?(:supports_gasp_api?) &&
               GaussianPoints::Hook.supports_gasp_api?
          UI.messagebox('Native GASP loading is not available in the current build.')
          return nil
        end

        item = GaussianPoints::UIparts::RenderItemRegistry.register_pointcloud_project(
          name: display_name || File.basename(filename, '.*'),
          project_path: filename,
          source_path: source_path || filename
        )
        if item
          UI.messagebox("Loaded #{File.basename(filename)}")
        else
          UI.messagebox('GASP project load failed.')
        end
        item
      end

      def register_pointcloud_points(name, points)
        return nil if points.empty?

        color = GaussianPoints.overlay&.visible_color || Sketchup::Color.new(80, 80, 80, 180)
        packed = points.flat_map { |pt| [pt.x.to_f, pt.y.to_f, pt.z.to_f, color.red.to_f, color.green.to_f, color.blue.to_f] }
        register_pointcloud_flat_data(name, packed)
      end

      def register_pointcloud_flat_data(name, flat_data, source_path: nil)
        return nil if flat_data.empty?

        prepared = prepare_centered_pointcloud(flat_data)
        project_path = source_path ? GaussianPoints::IO::GaspProject.cache_path_for_source(source_path) : nil
        if project_path
          written_project = GaussianPoints::IO::GaspProject.write_project(
            project_path: project_path,
            name: name,
            source_path: source_path,
            centered_points: prepared[:centered_data],
            center: prepared[:initial_state][:center],
            half_extents: prepared[:initial_state][:half_extents]
          )
          if written_project &&
             defined?(GaussianPoints::Hook) &&
             GaussianPoints::Hook.respond_to?(:supports_gasp_api?) &&
             GaussianPoints::Hook.supports_gasp_api?
            cached_item = GaussianPoints::UIparts::RenderItemRegistry.register_pointcloud_project(
              name: name,
              project_path: written_project,
              source_path: source_path
            )
            return cached_item if cached_item

            puts "[GASP] Native load failed for #{written_project}, falling back to direct point upload."
          end
        end

        GaussianPoints::UIparts::RenderItemRegistry.register_pointcloud(
          name: name,
          packed_points: prepared[:centered_data],
          initial_state: prepared[:initial_state]
        )
      end

      def pointcloud_bounds(flat_data)
        count = flat_data.length / 6
        min_x = max_x = flat_data[0].to_f
        min_y = max_y = flat_data[1].to_f
        min_z = max_z = flat_data[2].to_f

        count.times do |index|
          base = index * 6
          x = flat_data[base + 0].to_f
          y = flat_data[base + 1].to_f
          z = flat_data[base + 2].to_f
          min_x = [min_x, x].min
          min_y = [min_y, y].min
          min_z = [min_z, z].min
          max_x = [max_x, x].max
          max_y = [max_y, y].max
          max_z = [max_z, z].max
        end

        center = Geom::Point3d.new(
          (min_x + max_x) * 0.5,
          (min_y + max_y) * 0.5,
          (min_z + max_z) * 0.5
        )
        half_extents = {
          x: [((max_x - min_x) * 0.5), 1.mm].max,
          y: [((max_y - min_y) * 0.5), 1.mm].max,
          z: [((max_z - min_z) * 0.5), 1.mm].max
        }

        [center, half_extents]
      end

      def prepare_centered_pointcloud(flat_data)
        center, half_extents = pointcloud_bounds(flat_data)
        centered_data = Array.new(flat_data.length)
        count = flat_data.length / 6
        count.times do |index|
          base = index * 6
          centered_data[base + 0] = flat_data[base + 0].to_f - center.x
          centered_data[base + 1] = flat_data[base + 1].to_f - center.y
          centered_data[base + 2] = flat_data[base + 2].to_f - center.z
          centered_data[base + 3] = flat_data[base + 3].to_f
          centered_data[base + 4] = flat_data[base + 4].to_f
          centered_data[base + 5] = flat_data[base + 5].to_f
        end

        {
          centered_data: centered_data,
          initial_state: {
            center: center,
            half_extents: half_extents,
            axes: {
              x: Geom::Vector3d.new(1, 0, 0),
              y: Geom::Vector3d.new(0, 1, 0),
              z: Geom::Vector3d.new(0, 0, 1)
            }
          }
        }
      end

      def read_xyz_flat_data(filename)
        color = GaussianPoints.overlay&.visible_color || Sketchup::Color.new(80, 80, 80, 180)
        flat_data = []
        File.foreach(filename) do |line|
          next if line.strip.empty?

          cols = line.split
          next if cols.length < 3

          flat_data << cols[0].to_f
          flat_data << cols[1].to_f
          flat_data << cols[2].to_f
          if cols.length >= 6
            flat_data << cols[3].to_f
            flat_data << cols[4].to_f
            flat_data << cols[5].to_f
          else
            flat_data << color.red.to_f
            flat_data << color.green.to_f
            flat_data << color.blue.to_f
          end
        end
        flat_data
      end

      private

      def prepare_guided_import(filename)
        ext = File.extname(filename).downcase
        @pending_import = {
          filename: filename,
          ext: ext,
          point_count: nil,
          xyz_color_scale: 1.0,
          display_name: File.basename(filename),
          target_mode_label: 'Choose import mode',
          phase: :inspect_source,
          progress_percent: 0.0,
          ready: false,
          status: "Inspecting #{ext.sub('.', '').upcase} source",
          detail: 'Reading file information before import options become available.'
        }
        if ext == '.xyz'
          @pending_import[:inspect_file] = File.open(filename, 'r')
          @pending_import[:inspect_file_size] = File.size(filename).to_f
          @pending_import[:inspect_max_color] = 0.0
        end
        GaussianPoints::UIparts::ImportProgressDialog.show(dialog_payload_for_pending_import)
        schedule_pending_tick
      rescue StandardError => e
        UI.messagebox("Import preparation failed: #{e.message}")
      end

      def cleanup_pending_import
        pending = @pending_import
        return unless pending

        pending[:inspect_file]&.close
      rescue StandardError
        nil
      ensure
        @pending_import = nil
      end

      def schedule_pending_tick
        return unless @pending_import && !@pending_import[:ready]

        UI.start_timer(0.01, false) { process_pending_import_tick }
      end

      def process_pending_import_tick
        pending = @pending_import
        return unless pending
        return if pending[:ready]

        case pending[:ext]
        when '.e57'
          tick_pending_e57_inspect(pending)
        when '.xyz'
          tick_pending_xyz_inspect(pending)
        else
          finalize_pending_import_ready(pending)
        end

        GaussianPoints::UIparts::ImportProgressDialog.update(dialog_payload_for_pending_import) if @pending_import
        schedule_pending_tick if @pending_import && !@pending_import[:ready]
      rescue StandardError => e
        fail_pending_import("Import preparation failed: #{e.message}")
      end

      def tick_pending_e57_inspect(pending)
        count = GaussianPoints::IO::E57FiddleImporter.point_count(pending[:filename])
        pending[:point_count] = count if count && count >= 0
        pending[:progress_percent] = 100.0
        if pending[:point_count]
          pending[:detail] = "Detected #{format_integer(pending[:point_count])} points in the source file."
        else
          pending[:detail] = 'Point count is unavailable for this file, but import can still continue.'
        end
        finalize_pending_import_ready(pending)
      end

      def tick_pending_xyz_inspect(pending)
        file = pending[:inspect_file]
        return fail_pending_import('XYZ inspection file handle is unavailable.') unless file

        read_lines = 0
        while read_lines < XYZ_INSPECT_LINES_PER_TICK && (line = file.gets)
          next if line.strip.empty?

          cols = line.split
          next if cols.length < 3

          pending[:point_count] = pending[:point_count].to_i + 1
          if cols.length >= 6
            pending[:inspect_max_color] = [
              pending[:inspect_max_color].to_f,
              cols[3].to_f.abs,
              cols[4].to_f.abs,
              cols[5].to_f.abs
            ].max
          end
          read_lines += 1
        end

        file_size = [pending[:inspect_file_size].to_f, 1.0].max
        pending[:progress_percent] = ((file.pos.to_f / file_size) * 100.0).round(1)
        pending[:status] = 'Inspecting XYZ source'
        pending[:detail] = "Scanned #{format_integer(pending[:point_count])} points for import preparation."

        return unless file.eof?

        file.close
        pending[:inspect_file] = nil
        pending[:xyz_color_scale] = pending[:inspect_max_color].to_f > 1.001 ? 255.0 : 1.0
        finalize_pending_import_ready(pending)
      end

      def finalize_pending_import_ready(pending)
        pending[:ready] = true
        pending[:phase] = :ready
        pending[:progress_percent] = 0.0
        pending[:status] = 'Choose how to load the selected file'
        pending[:detail] = 'Direct load skips cache generation. Creating .gasp writes a fast reusable project file next to the source.'
      end

      def fail_pending_import(message)
        pending = @pending_import
        if pending
          pending[:phase] = :failed
          pending[:status] = 'Import preparation failed'
          pending[:detail] = message
          pending[:ready] = false
          GaussianPoints::UIparts::ImportProgressDialog.update(dialog_payload_for_pending_import)
        end
        cleanup_pending_import
        UI.messagebox(message)
      end

      def schedule_next_tick
        return unless @active_import

        UI.start_timer(0.01, false) { process_active_import_tick }
      end

      def process_active_import_tick
        session = @active_import
        return unless session

        case session[:phase]
        when :e57_native_import
          tick_e57_native_import(session)
        when :e57_transfer_points
          tick_e57_transfer(session)
        when :xyz_read
          tick_xyz_read(session)
        when :center_points
          tick_center_points(session)
        when :write_gasp
          tick_write_gasp(session)
        when :upload_direct
          finalize_direct_load(session)
        when :load_gasp
          finalize_gasp_load(session)
        end

        schedule_next_tick if @active_import
      rescue StandardError => e
        fail_active_import("Import failed: #{e.message}")
      end

      def tick_e57_native_import(session)
        status = GaussianPoints::IO::E57FiddleImporter.async_status
        total = [status[:total_points].to_i, session[:point_count].to_i].max
        processed = status[:processed_points].to_i
        session[:progress_percent] = stage_progress(processed, total, 0.0, 45.0)
        session[:status] = 'Reading E57 source'
        session[:detail] = "Imported #{format_integer(processed)} / #{format_integer(total)} points from source."
        GaussianPoints::UIparts::ImportProgressDialog.update(dialog_payload_for_active_session)

        case status[:state]
        when E57_STATE_RUNNING
          nil
        when E57_STATE_COMPLETED
          session[:phase] = :e57_transfer_points
          session[:fetch_index] = 0
          session[:point_count] = status[:result_count].to_i
          session[:status] = 'Transferring points'
          session[:detail] = 'Copying imported points from native memory.'
        when E57_STATE_FAILED
          fail_active_import("E57 import failed: #{GaussianPoints::IO::E57FiddleImporter.last_error}")
        end
      end

      def tick_e57_transfer(session)
        remaining = session[:point_count].to_i - session[:fetch_index].to_i
        chunk_size = [remaining, E57_FETCH_POINTS_PER_TICK].min
        chunk = GaussianPoints::IO::E57FiddleImporter.fetch_points_chunk(session[:fetch_index], chunk_size)
        append_e57_chunk(session, chunk)
        session[:fetch_index] += chunk.length
        session[:progress_percent] = stage_progress(session[:fetch_index], session[:point_count], 45.0, 70.0)
        session[:status] = 'Transferring points'
        session[:detail] = "Transferred #{format_integer(session[:fetch_index])} / #{format_integer(session[:point_count])} points."
        GaussianPoints::UIparts::ImportProgressDialog.update(dialog_payload_for_active_session)

        transition_to_centering(session) if session[:fetch_index] >= session[:point_count]
      end

      def tick_xyz_read(session)
        file = session[:xyz_file]
        return fail_active_import('XYZ file handle is unavailable.') unless file

        read_lines = 0
        while read_lines < XYZ_READ_LINES_PER_TICK && (line = file.gets)
          next if line.strip.empty?

          cols = line.split
          next if cols.length < 3

          x = cols[0].to_f
          y = cols[1].to_f
          z = cols[2].to_f
          if cols.length >= 6
            color_scale = session[:xyz_color_scale].to_f
            r = normalize_color_value(cols[3].to_f, color_scale)
            g = normalize_color_value(cols[4].to_f, color_scale)
            b = normalize_color_value(cols[5].to_f, color_scale)
          else
            color = GaussianPoints.overlay&.visible_color || Sketchup::Color.new(80, 80, 80, 180)
            r = color.red.to_f / 255.0
            g = color.green.to_f / 255.0
            b = color.blue.to_f / 255.0
          end

          append_flat_point(session, x, y, z, r, g, b)
          read_lines += 1
        end

        file_size = [session[:xyz_file_size].to_f, 1.0].max
        progress = file.pos.to_f / file_size
        session[:progress_percent] = (progress * 70.0).round(1)
        session[:status] = 'Reading XYZ source'
        session[:detail] = "Read #{format_integer(session[:flat_data].length / 6)} / #{format_integer(session[:point_count])} points."
        GaussianPoints::UIparts::ImportProgressDialog.update(dialog_payload_for_active_session)

        if file.eof?
          file.close
          session[:xyz_file] = nil
          transition_to_centering(session)
        end
      end

      def transition_to_centering(session)
        session[:phase] = :center_points
        center, half_extents = bounds_to_center_and_half_extents(session)
        session[:initial_state] = {
          center: center,
          half_extents: half_extents,
          axes: {
            x: Geom::Vector3d.new(1, 0, 0),
            y: Geom::Vector3d.new(0, 1, 0),
            z: Geom::Vector3d.new(0, 0, 1)
          }
        }
        session[:centered_data] = Array.new(session[:flat_data].length)
        session[:center_index] = 0
        session[:status] = 'Centering points'
        session[:detail] = 'Preparing local point cloud coordinates.'
      end

      def tick_center_points(session)
        point_count = session[:flat_data].length / 6
        start_index = session[:center_index].to_i
        finish_index = [start_index + CENTER_POINTS_PER_TICK, point_count].min
        center = session[:initial_state][:center]

        index = start_index
        while index < finish_index
          base = index * 6
          session[:centered_data][base + 0] = session[:flat_data][base + 0].to_f - center.x
          session[:centered_data][base + 1] = session[:flat_data][base + 1].to_f - center.y
          session[:centered_data][base + 2] = session[:flat_data][base + 2].to_f - center.z
          session[:centered_data][base + 3] = session[:flat_data][base + 3].to_f
          session[:centered_data][base + 4] = session[:flat_data][base + 4].to_f
          session[:centered_data][base + 5] = session[:flat_data][base + 5].to_f
          index += 1
        end

        session[:center_index] = finish_index
        session[:progress_percent] = stage_progress(finish_index, point_count, 70.0, session[:create_gasp] ? 88.0 : 96.0)
        session[:status] = 'Centering points'
        session[:detail] = "Prepared #{format_integer(finish_index)} / #{format_integer(point_count)} local points."
        GaussianPoints::UIparts::ImportProgressDialog.update(dialog_payload_for_active_session)

        return unless finish_index >= point_count

        session[:flat_data] = nil
        if session[:create_gasp]
          session[:phase] = :write_gasp
          project_path = GaussianPoints::IO::GaspProject.cache_path_for_source(session[:filename])
          session[:gasp_write_session] = GaussianPoints::IO::GaspProject.begin_write_session(
            project_path: project_path,
            name: session[:base_name],
            source_path: session[:filename],
            centered_points: session[:centered_data],
            center: session[:initial_state][:center],
            half_extents: session[:initial_state][:half_extents]
          )
          return fail_active_import('Failed to start .gasp writer.') unless session[:gasp_write_session]
        else
          session[:phase] = :upload_direct
        end
      end

      def tick_write_gasp(session)
        result = GaussianPoints::IO::GaspProject.write_session_chunk(session[:gasp_write_session])
        return fail_active_import('Failed to write .gasp chunk.') unless result

        session[:progress_percent] = stage_progress(result[:written_points], result[:total_points], 88.0, 99.0)
        session[:status] = 'Writing .gasp cache'
        session[:detail] = "Wrote #{format_integer(result[:written_points])} / #{format_integer(result[:total_points])} points to cache."
        GaussianPoints::UIparts::ImportProgressDialog.update(dialog_payload_for_active_session)

        return unless result[:done]

        session[:project_path] = GaussianPoints::IO::GaspProject.finish_write_session(session[:gasp_write_session])
        session[:gasp_write_session] = nil
        session[:phase] = :load_gasp
      end

      def finalize_direct_load(session)
        session[:progress_percent] = 99.0
        session[:status] = 'Uploading point cloud'
        session[:detail] = 'Sending centered points to the renderer.'
        GaussianPoints::UIparts::ImportProgressDialog.update(dialog_payload_for_active_session)

        item = GaussianPoints::UIparts::RenderItemRegistry.register_pointcloud(
          name: session[:base_name],
          packed_points: session[:centered_data],
          initial_state: session[:initial_state]
        )
        return fail_active_import('Point cloud upload failed.') unless item

        complete_active_import("Imported #{format_integer(session[:point_count])} points from #{session[:display_name]}.")
      end

      def finalize_gasp_load(session)
        session[:progress_percent] = 99.5
        session[:status] = 'Loading generated .gasp'
        session[:detail] = 'Opening the fresh cached project.'
        GaussianPoints::UIparts::ImportProgressDialog.update(dialog_payload_for_active_session)

        item = GaussianPoints::UIparts::RenderItemRegistry.register_pointcloud_project(
          name: session[:base_name],
          project_path: session[:project_path],
          source_path: session[:filename]
        )
        return fail_active_import('Generated .gasp could not be loaded.') unless item

        complete_active_import("Created and loaded #{File.basename(session[:project_path])}.")
      end

      def complete_active_import(message)
        session = @active_import
        return unless session

        session[:phase] = :done
        session[:progress_percent] = 100.0
        session[:status] = 'Import finished'
        session[:detail] = message
        GaussianPoints::UIparts::ImportProgressDialog.update(dialog_payload_for_active_session)
        UI.messagebox(message)
        @active_import = nil
      end

      def fail_active_import(message)
        session = @active_import || {}
        session[:phase] = :failed
        session[:progress_percent] = 100.0
        session[:status] = 'Import failed'
        session[:detail] = message
        GaussianPoints::UIparts::ImportProgressDialog.update(dialog_payload_for_session(session, show_choices: false))
        UI.messagebox(message)
        @active_import = nil
      end

      def append_e57_chunk(session, chunk)
        scale_factor = 39.3700787
        chunk.each do |pt, r, g, b|
          append_flat_point(
            session,
            pt.x * scale_factor,
            pt.y * scale_factor,
            pt.z * scale_factor,
            r.to_f / 255.0,
            g.to_f / 255.0,
            b.to_f / 255.0
          )
        end
      end

      def append_flat_point(session, x, y, z, r, g, b)
        session[:flat_data] << x
        session[:flat_data] << y
        session[:flat_data] << z
        session[:flat_data] << r
        session[:flat_data] << g
        session[:flat_data] << b

        session[:min_x] = session[:min_x].nil? ? x : [session[:min_x], x].min
        session[:min_y] = session[:min_y].nil? ? y : [session[:min_y], y].min
        session[:min_z] = session[:min_z].nil? ? z : [session[:min_z], z].min
        session[:max_x] = session[:max_x].nil? ? x : [session[:max_x], x].max
        session[:max_y] = session[:max_y].nil? ? y : [session[:max_y], y].max
        session[:max_z] = session[:max_z].nil? ? z : [session[:max_z], z].max
      end

      def bounds_to_center_and_half_extents(session)
        center = Geom::Point3d.new(
          (session[:min_x] + session[:max_x]) * 0.5,
          (session[:min_y] + session[:max_y]) * 0.5,
          (session[:min_z] + session[:max_z]) * 0.5
        )
        half_extents = {
          x: [((session[:max_x] - session[:min_x]) * 0.5), 1.mm].max,
          y: [((session[:max_y] - session[:min_y]) * 0.5), 1.mm].max,
          z: [((session[:max_z] - session[:min_z]) * 0.5), 1.mm].max
        }
        [center, half_extents]
      end

      def normalize_color_value(value, source_scale)
        scale = source_scale.to_f
        return value.to_f.clamp(0.0, 1.0) if scale <= 1.001

        (value.to_f / scale).clamp(0.0, 1.0)
      end

      def format_integer(value)
        value.to_i.to_s.reverse.gsub(/(\d{3})(?=\d)/, '\\1 ').reverse
      end

      def stage_progress(done, total, start_percent, end_percent)
        return end_percent if total.to_i <= 0

        clamped = [[done.to_f / total.to_f, 0.0].max, 1.0].min
        (start_percent + (end_percent - start_percent) * clamped).round(1)
      end

      def dialog_payload_for_pending_import
        dialog_payload_for_session(
          {
            create_gasp: false,
            display_name: @pending_import[:display_name] || File.basename(@pending_import[:filename]),
            ext: @pending_import[:ext],
            point_count: @pending_import[:point_count],
            target_mode_label: @pending_import[:target_mode_label] || 'Choose import mode',
            phase: @pending_import[:phase] || :inspect_source,
            progress_percent: @pending_import[:progress_percent].to_f,
            status: @pending_import[:status] || 'Preparing import',
            detail: @pending_import[:detail] || ''
          },
          show_choices: @pending_import[:ready],
          progress_percent: @pending_import[:progress_percent].to_f
        )
      end

      def dialog_payload_for_active_session
        dialog_payload_for_session(@active_import, show_choices: false)
      end

      def dialog_payload_for_session(session, show_choices: false, progress_percent: nil)
        {
          title: 'Point Cloud Import',
          subtitle: session[:create_gasp] ? 'Fast cache build path' : 'Direct point cloud load path',
          file_name: session[:display_name],
          file_type: session[:ext].to_s.sub('.', '').upcase,
          point_count_label: session[:point_count] ? format_integer(session[:point_count]) : 'Unknown',
          target_mode: session[:target_mode_label] || 'Choose import mode',
          status: session[:status] || 'Waiting for action',
          detail: session[:detail] || '',
          phase: (session[:phase] || :idle).to_s.tr('_', ' ').capitalize,
          progress_percent: progress_percent || session[:progress_percent].to_f,
          progress_label: "#{(progress_percent || session[:progress_percent].to_f).round(1)}%",
          show_choices: show_choices
        }
      end
    end
  end
end
