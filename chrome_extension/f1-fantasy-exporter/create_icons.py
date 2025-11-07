from PIL import Image, ImageDraw, ImageFont

def create_icon(size):
    # Create image with F1 red background
    img = Image.new('RGB', (size, size), '#e10600')
    draw = ImageDraw.Draw(img)
    
    # Draw white "F1" text
    try:
        # Try to use a built-in font, but PIL might not have it
        font_size = int(size * 0.4)
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", font_size)
    except:
        # Fallback to default font
        font = ImageFont.load_default()
    
    text = "F1"
    bbox = draw.textbbox((0, 0), text, font=font)
    text_width = bbox[2] - bbox[0]
    text_height = bbox[3] - bbox[1]
    
    position = ((size - text_width) // 2, (size - text_height) // 2 - 5)
    draw.text(position, text, fill='white', font=font)
    
    return img

# Create icons
for size in [16, 48, 128]:
    icon = create_icon(size)
    icon.save(f'icon{size}.png')
    print(f'Created icon{size}.png')

