# NOTE: The Infinity-Parser2 model will be automatically downloaded on the first run.

from infinity_parser2 import InfinityParser2

parser = InfinityParser2()

# Parse a single file (returns Markdown)
result = parser.parse("assets/national_geographic/المومياوات ناشيونال جيوجرافيك .pdf"
    ,task_type="doc2md")

print(result)
