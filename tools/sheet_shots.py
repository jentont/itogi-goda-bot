import os, sys
from PIL import Image
T, S = sys.argv[1], sys.argv[2]
names=['hold','sub']+[f's{i}' for i in range(12)]
ims=[Image.open(os.path.join(T,n+'.png')).convert('RGB') for n in names]
w,h=ims[0].size
for part,chunk in enumerate((ims[:7], ims[7:])):
    sheet=Image.new('RGB',(w*7+60,h),(10,10,10))
    for i,im in enumerate(chunk): sheet.paste(im,(i*(w+10),0))
    sheet.thumbnail((2200,2200)); sheet.save(os.path.join(S,f'app_{part}.jpg'),quality=85)
