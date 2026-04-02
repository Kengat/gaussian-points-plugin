# io/fiddle_importer.rb
require 'fiddle'
require 'fiddle/import'

module GaussianPoints
  module IO

    module E57FiddleImporter

      # Настраиваем загрузку DLL
      def self.setup_dll
        script_path = File.expand_path(__FILE__)
        plugin_dir  = File.dirname(script_path)
        # DLL находится на уровень выше (из папки /io в корень плагина)
        dll_path    = File.join(plugin_dir, "..", "E57ImporterDLL.dll")

        unless File.exist?(dll_path)
          UI.messagebox("Не найден DLL: #{dll_path}")
          @dll_loaded = false
          return
        end

        @dll = Fiddle.dlopen(dll_path)
        puts "DLL загружен успешно: #{dll_path}"
        @dll_loaded = true

        # Сигнатура функции: int importE57(const char* filename)
        @importE57 = Fiddle::Function.new(
          @dll['importE57'],
          [Fiddle::TYPE_VOIDP],  # (char*) – C-строка
          Fiddle::TYPE_INT       # возвращает int
        )

        # Сигнатура функции: bool getPointData(int, double*, double*, double*, uint8_t*, uint8_t*, uint8_t*)
        @getPointData = Fiddle::Function.new(
          @dll['getPointData'],
          [
            Fiddle::TYPE_INT,
            Fiddle::TYPE_VOIDP, Fiddle::TYPE_VOIDP, Fiddle::TYPE_VOIDP,
            Fiddle::TYPE_VOIDP, Fiddle::TYPE_VOIDP, Fiddle::TYPE_VOIDP
          ],
          Fiddle::TYPE_INT
        )
      end

      # Вызываем setup_dll один раз при загрузке
      setup_dll

      # Функция импорта: возвращает массив точек с цветом: [[pt, rr, gg, bb], ...]
      def self.import_file(filename)
        return [] unless @dll_loaded

        # Подготавливаем C-строку с завершающим \0
        c_str = (filename + "\0").force_encoding("ASCII-8BIT")
        ptr   = Fiddle::Pointer.to_ptr(c_str)

        count = @importE57.call(ptr)
        if count < 0
          UI.messagebox("Ошибка importE57 (см. консоль).")
          return []
        end
        if count == 0
          puts "[fiddle_importer] E57: 0 points"
          return []
        end

        result_points = []

        buf_x = Fiddle::Pointer.malloc(Fiddle::SIZEOF_DOUBLE)
        buf_y = Fiddle::Pointer.malloc(Fiddle::SIZEOF_DOUBLE)
        buf_z = Fiddle::Pointer.malloc(Fiddle::SIZEOF_DOUBLE)
        buf_r = Fiddle::Pointer.malloc(1) # uint8_t (1 байт)
        buf_g = Fiddle::Pointer.malloc(1)
        buf_b = Fiddle::Pointer.malloc(1)

        (0...count).each do |i|
          ret = @getPointData.call(
            i, buf_x, buf_y, buf_z,
            buf_r, buf_g, buf_b
          )
          break if ret == 0

          x = buf_x[0,8].unpack("d").first
          y = buf_y[0,8].unpack("d").first
          z = buf_z[0,8].unpack("d").first
          # Приводим байт к значению от 0 до 255:
          rr = buf_r[0].ord & 0xFF
          gg = buf_g[0].ord & 0xFF
          bb = buf_b[0].ord & 0xFF

          result_points << [Geom::Point3d.new(x, y, z), rr, gg, bb]
        end

        puts "[fiddle_importer] импортировано #{result_points.size} точек E57"
        result_points
      end

    end

  end
end
