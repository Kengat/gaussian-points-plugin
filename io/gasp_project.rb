require 'fileutils'

module GaussianPoints
  module IO
    module GaspProject
      extend self

      MAGIC = 'GASP'.b.freeze
      VERSION = 2
      FLAGS = 0
      FULL_POINT_STRIDE = 7
      PREVIEW_POINT_STRIDE = 7
      WRITE_CHUNK_POINTS = 50_000

      def cache_path_for_source(source_path)
        return nil if source_path.nil? || source_path.empty?

        base = File.join(File.dirname(source_path), File.basename(source_path, '.*'))
        "#{base}.gasp"
      end

      def fresh_cache_for(source_path)
        cache_path = cache_path_for_source(source_path)
        return nil unless cache_path && File.exist?(cache_path) && File.exist?(source_path)
        return nil if File.mtime(cache_path) < File.mtime(source_path)
        return nil unless compatible_cache_file?(cache_path)

        cache_path
      rescue StandardError
        nil
      end

      def write_project(project_path:, name:, source_path:, centered_points:, center:, half_extents:)
        return nil if project_path.nil? || project_path.empty?
        return nil if centered_points.nil? || centered_points.empty?

        full_count = centered_points.length / 6
        return nil if full_count <= 0

        preview_count = 0
        name_bytes = (name || '').to_s.encode(Encoding::UTF_8)
        source_bytes = (source_path || '').to_s.encode(Encoding::UTF_8)

        FileUtils.mkdir_p(File.dirname(project_path))
        File.open(project_path, 'wb') do |io|
          io.write(MAGIC)
          io.write([VERSION, FLAGS].pack('V2'))
          io.write([full_count, preview_count].pack('Q<Q<'))
          io.write([FULL_POINT_STRIDE, PREVIEW_POINT_STRIDE, name_bytes.bytesize, source_bytes.bytesize].pack('V4'))
          io.write([
            center.x.to_f, center.y.to_f, center.z.to_f,
            half_extents[:x].to_f, half_extents[:y].to_f, half_extents[:z].to_f
          ].pack('E6'))
          io.write(name_bytes)
          io.write(source_bytes)
          write_point_buffer(io, centered_points, step: 1)
        end
        project_path
      rescue StandardError => e
        puts "[GASP] Failed to write #{project_path}: #{e.message}"
        nil
      end

      def begin_write_session(project_path:, name:, source_path:, centered_points:, center:, half_extents:)
        return nil if project_path.nil? || project_path.empty?
        return nil if centered_points.nil? || centered_points.empty?

        full_count = centered_points.length / 6
        return nil if full_count <= 0

        preview_count = 0
        name_bytes = (name || '').to_s.encode(Encoding::UTF_8)
        source_bytes = (source_path || '').to_s.encode(Encoding::UTF_8)

        FileUtils.mkdir_p(File.dirname(project_path))
        io = File.open(project_path, 'wb')
        io.write(MAGIC)
        io.write([VERSION, FLAGS].pack('V2'))
        io.write([full_count, preview_count].pack('Q<Q<'))
        io.write([FULL_POINT_STRIDE, PREVIEW_POINT_STRIDE, name_bytes.bytesize, source_bytes.bytesize].pack('V4'))
        io.write([
          center.x.to_f, center.y.to_f, center.z.to_f,
          half_extents[:x].to_f, half_extents[:y].to_f, half_extents[:z].to_f
        ].pack('E6'))
        io.write(name_bytes)
        io.write(source_bytes)

        {
          io: io,
          project_path: project_path,
          centered_points: centered_points,
          point_count: full_count,
          next_index: 0
        }
      rescue StandardError => e
        puts "[GASP] Failed to start write session for #{project_path}: #{e.message}"
        nil
      end

      def write_session_chunk(session, chunk_points: WRITE_CHUNK_POINTS)
        return nil unless session && session[:io]

        io = session[:io]
        centered_points = session[:centered_points]
        point_count = session[:point_count].to_i
        start_index = session[:next_index].to_i
        return { done: true, written_points: point_count, total_points: point_count } if start_index >= point_count

        finish_index = [start_index + chunk_points, point_count].min
        chunk = []
        index = start_index
        while index < finish_index
          base = index * 6
          chunk << centered_points[base + 0].to_f
          chunk << centered_points[base + 1].to_f
          chunk << centered_points[base + 2].to_f
          chunk << normalized_color_component(centered_points[base + 3])
          chunk << normalized_color_component(centered_points[base + 4])
          chunk << normalized_color_component(centered_points[base + 5])
          chunk << 1.0
          index += 1
        end
        io.write(chunk.pack('e*')) unless chunk.empty?
        session[:next_index] = finish_index

        {
          done: finish_index >= point_count,
          written_points: finish_index,
          total_points: point_count
        }
      end

      def finish_write_session(session)
        return nil unless session

        session[:io]&.close
        session[:project_path]
      rescue StandardError => e
        puts "[GASP] Failed to finish write session: #{e.message}"
        nil
      end

      private

      def compatible_cache_file?(project_path)
        File.open(project_path, 'rb') do |io|
          header = io.read(28)
          return false unless header && header.bytesize == 28

          magic, version, _flags, _full_count, preview_count = header.unpack('a4V2Q<Q<')
          magic == MAGIC && version == VERSION && preview_count == 0
        end
      rescue StandardError
        false
      end

      def write_point_buffer(io, centered_points, step:)
        chunk = []
        point_count = centered_points.length / 6
        index = 0
        while index < point_count
          base = index * 6
          chunk << centered_points[base + 0].to_f
          chunk << centered_points[base + 1].to_f
          chunk << centered_points[base + 2].to_f
          chunk << normalized_color_component(centered_points[base + 3])
          chunk << normalized_color_component(centered_points[base + 4])
          chunk << normalized_color_component(centered_points[base + 5])
          chunk << 1.0
          if chunk.length >= WRITE_CHUNK_POINTS * FULL_POINT_STRIDE
            io.write(chunk.pack('e*'))
            chunk.clear
          end
          index += step
        end
        io.write(chunk.pack('e*')) unless chunk.empty?
      end

      def normalized_color_component(value)
        color = value.to_f
        color > 1.001 ? (color / 255.0) : color.clamp(0.0, 1.0)
      end
    end
  end
end
