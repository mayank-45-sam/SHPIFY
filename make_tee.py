from PIL import Image, ImageDraw
img = Image.new('RGBA', (600, 750), (255,255,255,255))
draw = ImageDraw.Draw(img)
draw.rectangle([150, 200, 450, 550], fill=(255,255,255), outline=(210,210,210))
draw.polygon([(150,200),(200,150),(400,150),(450,200)], fill=(255,255,255), outline=(210,210,210))
draw.ellipse([200,150,400,180], fill=(255,255,255), outline=(210,210,210))
img.save('static/img/custom/white-tee.png')
print('done')
