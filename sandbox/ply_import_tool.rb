# ply_import_tool.rb - Плагин для загрузки PLY файлов в SketchUp
require 'sketchup.rb'
require 'fiddle'

module PLYLoader
  def self.to_c_string(value)
    Fiddle::Pointer[(value.encode('UTF-8') + "\0")]
  end

  def self.resolve_ply_importer_path(plugin_dir)
    candidates = [
      File.join(plugin_dir, 'cpp', 'build', 'PlyImporter', 'PlyImporter', 'x64', 'Release', 'PlyImporter.dll'),
      File.join(plugin_dir, 'PlyImporter.dll')
    ]

    candidates.find { |path| File.exist?(path) }
  end

  # Загрузка DLL для импорта PLY
  def self.setup_dll
    script_path = File.expand_path(__FILE__)
    plugin_dir = File.dirname(script_path)
    ply_dll_path = resolve_ply_importer_path(plugin_dir)
    
    # Загружаем PLY Importer DLL
    unless ply_dll_path && File.exist?(ply_dll_path)
      UI.messagebox("Не найден PLY Importer DLL")
      @ply_dll_loaded = false
      return
    end
    
    @ply_dll = Fiddle.dlopen(ply_dll_path)
    puts "PLY Importer DLL загружен успешно: #{ply_dll_path}"
    @ply_dll_loaded = true
    
    # Функция для загрузки PLY файла
    @load_ply_file = Fiddle::Function.new(
      @ply_dll['LoadPLYFile'],
      [Fiddle::TYPE_VOIDP],
      Fiddle::TYPE_BOOL
    )
    
    puts "Функция LoadPLYFile получена из PLY Importer DLL"
  end
  
  # Вызываем setup_dll при загрузке скрипта
  setup_dll
  
  # Функция для показа диалога выбора файла и загрузки PLY
  def self.load_ply
    return unless @ply_dll_loaded
    
    # Показываем диалог выбора файла
    filename = UI.openpanel("Выберите PLY файл", "", "PLY файлы|*.ply||")
    return if filename.nil? || filename.empty?
    
    puts "Выбран файл: #{filename}"
    
    # Преобразуем путь к файлу в формат C-строки
    c_filename = to_c_string(filename.tr('/', '\\'))
    
    # Вызываем функцию загрузки PLY из DLL
    result = @load_ply_file.call(c_filename)
    
    if result
      puts "PLY файл успешно загружен и проанализирован"
    else
      puts "Ошибка при загрузке PLY файла"
    end
  end
end

# Создаем команду в меню
unless file_loaded?(__FILE__)
  menu = UI.menu("Plugins").add_submenu("PLY Loader")
  menu.add_item("Загрузить PLY файл") { PLYLoader.load_ply }
  
  # Создаем кнопку на панели инструментов
  toolbar = UI::Toolbar.new("PLY Loader")
  
  cmd = UI::Command.new("Загрузить PLY") { PLYLoader.load_ply }
  cmd.tooltip = "Загрузить PLY файл с гауссовыми кляксами"
  cmd.status_bar_text = "Загрузить PLY файл с гауссовыми кляксами для анализа"
  cmd.large_icon = "path_to_your_icon.png" # Замените на путь к вашей иконке
  cmd.small_icon = "path_to_your_small_icon.png" # Замените на путь к маленькой иконке
  
  toolbar.add_item(cmd)
  toolbar.show
  
  file_loaded(__FILE__)
end
