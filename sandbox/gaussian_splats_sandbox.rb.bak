# gaussian_splats_sandbox.rb - Песочница для работы с тремя DLL
require 'fiddle'
require 'fiddle/import'
require 'sketchup.rb'

# Класс для создания оверлея, который рисует точку в центре экрана
class CenterPointOverlay < Sketchup::Overlay
  def initialize
    # Указываем уникальный идентификатор, имя и краткое описание
    super('my_extension.center_point_overlay', 'Center Point Overlay', description: 'Отображает точку в центре экрана')
  end
  
  # Для корректной отрисовки оверлея необходимо задать экстенты,
  # чтобы SketchUp не обрезал нашу 2D-отрисовку.
  def getExtents
    bb = Geom::BoundingBox.new
    # Задаём достаточно большую область
    bb.add(Geom::Point3d.new(-10000, -10000, 0))
    bb.add(Geom::Point3d.new(10000, 10000, 0))
    bb
  end
  
  # Метод draw вызывается при перерисовке вида
  def draw(view)
    # Определяем центр вьюпорта (логические пиксели)
    center_x = view.vpwidth / 2.0
    center_y = view.vpheight / 2.0
    size = 5  # половина размера квадрата, итоговый квадрат 10x10 пикселей
    square = [
      [center_x - size, center_y - size, 0],
      [center_x + size, center_y - size, 0],
      [center_x + size, center_y + size, 0],
      [center_x - size, center_y + size, 0]
    ]
    view.drawing_color = 'red'
    view.draw2d(GL_QUADS, square)
  end
end

module GaussianSplats
  # Настройка и загрузка всех DLL
  def self.setup_dlls
    script_path = File.expand_path(__FILE__)
    plugin_dir = File.dirname(script_path)
    path_entries = ENV.fetch('PATH', '').split(File::PATH_SEPARATOR)
    unless path_entries.include?(plugin_dir)
      ENV['PATH'] = ([plugin_dir] + path_entries).join(File::PATH_SEPARATOR)
    end
    @support_dlls = []
    
    %w[glew32.dll minhook.x64.dll].each do |dll_name|
      dll_path = File.join(plugin_dir, dll_name)
      next unless File.exist?(dll_path)
    
      @support_dlls << Fiddle.dlopen(dll_path)
    end
    
    # Загрузка Hook DLL
    hook_dll_path = File.join(plugin_dir, "SketchUpOverlayBridge.dll")
    unless File.exist?(hook_dll_path)
      UI.messagebox("Не найден Hook DLL: #{hook_dll_path}")
      @hook_dll_loaded = false
      return
    end
    
    @hook_dll = Fiddle.dlopen(hook_dll_path)
    puts "Hook DLL загружен успешно: #{hook_dll_path}"
    @hook_dll_loaded = true
    
    # Загрузка Renderer DLL
    renderer_dll_path = File.join(plugin_dir, "GaussianSplatRenderer.dll")
    unless File.exist?(renderer_dll_path)
      UI.messagebox("Не найден Renderer DLL: #{renderer_dll_path}")
      @renderer_dll_loaded = false
      return
    end
    
    @renderer_dll = Fiddle.dlopen(renderer_dll_path)
    puts "Renderer DLL загружен успешно: #{renderer_dll_path}"
    @renderer_dll_loaded = true
    
    # Загрузка PLY Importer DLL
    ply_dll_path = File.join(plugin_dir, "PlyImporter.dll")
    unless File.exist?(ply_dll_path)
      UI.messagebox("Не найден PLY Importer DLL: #{ply_dll_path}")
      @ply_dll_loaded = false
      return
    else
      @ply_dll = Fiddle.dlopen(ply_dll_path)
      puts "PLY Importer DLL загружен успешно: #{ply_dll_path}"
      @ply_dll_loaded = true
    end
    
    # Определяем функции из DLL
    if @hook_dll_loaded
      @installAllHooks = Fiddle::Function.new(
        @hook_dll['InstallAllHooks'],
        [],
        Fiddle::TYPE_VOID
      )
    end
    
    if @renderer_dll_loaded
      @renderPointCloud = Fiddle::Function.new(
        @renderer_dll['renderPointCloud'],
        [],
        Fiddle::TYPE_VOID
      )
      
      @clearSplats = Fiddle::Function.new(
        @renderer_dll['ClearSplats'],
        [],
        Fiddle::TYPE_VOID
      )
      
      @loadSplatsFromPLY = Fiddle::Function.new(
        @renderer_dll['LoadSplatsFromPLY'],
        [Fiddle::TYPE_VOIDP],
        Fiddle::TYPE_VOID
      )
    end
    
    if @ply_dll_loaded
      @loadPLYFile = Fiddle::Function.new(
        @ply_dll['LoadPLYFile'],
        [Fiddle::TYPE_VOIDP],
        Fiddle::TYPE_INT
      )
    end
  end
  
  # Инициализация модуля
  setup_dlls
  
  # Инициализация хуков
  def self.init_hooks
    return unless @hook_dll_loaded
    
    # Вызываем функцию установки хуков
    @installAllHooks.call
    puts "Хуки установлены"
  end
  
  # Принудительный вызов рендеринга
  def self.render_splats
    return unless @renderer_dll_loaded
    
    puts "Принудительный вызов renderPointCloud..."
    @renderPointCloud.call
    puts "renderPointCloud выполнен"
  end
  
  # Очистка клякс
  def self.clear_splats
    return unless @renderer_dll_loaded
    
    puts "Очистка клякс..."
    @clearSplats.call
    puts "Кляксы очищены"
  end
  
  # Анализ PLY файла
  def self.analyze_ply
    return unless @ply_dll_loaded
    
    # Диалог выбора файла
    filename = UI.openpanel("Выберите PLY файл", "", "PLY файлы|*.ply||")
    return if filename.nil? || filename.empty?
    
    puts "Выбран файл: #{filename}"
    
    # Преобразование пути к файлу для C-строки
    c_filename = filename.gsub('/', '\\')
    
    # Анализ файла
    result = @loadPLYFile.call(c_filename)
    
    if result != 0
      puts "PLY файл успешно проанализирован"
    else
      puts "Ошибка при анализе PLY файла"
    end
  end
  
  # Загрузка клякс из PLY
  def self.load_ply_splats
    return unless @renderer_dll_loaded
    
    # Диалог выбора файла
    filename = UI.openpanel("Выберите PLY файл для загрузки клякс", "", "PLY файлы|*.ply||")
    return if filename.nil? || filename.empty?
    
    puts "Выбран файл: #{filename}"
    
    # Преобразование пути к файлу для C-строки
    c_filename = filename.gsub('/', '\\')
    
    # Загрузка клякс из PLY
    @loadSplatsFromPLY.call(c_filename)
    
    puts "Кляксы загружены из PLY файла"
  end
  
  # Инициализация плагина
  def self.init_plugin
    puts "Инициализация плагина гауссовых клякс..."
    init_hooks
    puts "Плагин гауссовых клякс инициализирован"
  end
  
  # Остановка плагина
  def self.stop_plugin
    puts "Плагин гауссовых клякс остановлен"
    clear_splats
  end
end

# Команды для меню
unless file_loaded?(__FILE__)
  menu = UI.menu("Plugins").add_submenu("Gaussian Splats")
  menu.add_item("Инициализировать") { GaussianSplats.init_plugin }
  menu.add_item("Загрузить PLY (анализ)") { GaussianSplats.analyze_ply }
  menu.add_item("Загрузить кляксы из PLY") { GaussianSplats.load_ply_splats }
  menu.add_item("Показать кляксы") { GaussianSplats.render_splats }
  menu.add_item("Очистить кляксы") { GaussianSplats.clear_splats }
  menu.add_item("Остановить") { GaussianSplats.stop_plugin }
  
  # Создаём экземпляр оверлея центральной точки
  overlay = CenterPointOverlay.new
  # Добавляем оверлей в активную модель и активируем его
  model = Sketchup.active_model
  model.overlays.add(overlay)
  overlay.enabled = true
  puts "Центральная точка добавлена через оверлей"
  
  # Автозапуск при загрузке
  GaussianSplats.init_plugin
  
  file_loaded(__FILE__)
end

