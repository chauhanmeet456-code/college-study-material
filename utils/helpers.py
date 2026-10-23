from werkzeug.utils import secure_filename
from config import Config

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in Config.ALLOWED_EXTENSIONS

def allowed_image_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in Config.ALLOWED_IMAGE_EXTENSIONS

def is_video_file(filename):
    if '.' not in filename:
        return False
    ext = filename.rsplit('.', 1)[1].lower()
    return ext in {'mp4', 'webm', 'ogg', 'avi', 'mov', 'wmv', 'flv', 'mkv'}

def generate_safe_filename(filename):
    filename = secure_filename(filename)
    import uuid
    ext = filename.rsplit('.', 1)[1].lower() if '.' in filename else ''
    return f"{uuid.uuid4().hex}.{ext}"

def get_category_subdir(category_name):
    mapping = {
        'Notes': 'notes',
        'Assignment': 'assignments',
        'Book': 'books',
        'Practical': 'practicals',
        'Question Paper': 'question_papers',
        'Important Question': 'important_questions',
        'Syllabus': 'syllabus',
        'Other': 'other'
    }
    return mapping.get(category_name, 'other')

def format_file_size(size_bytes):
    if size_bytes is None:
        return '0 B'
    for unit in ['B', 'KB', 'MB', 'GB']:
        if size_bytes < 1024.0:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024.0
    return f"{size_bytes:.1f} TB"
