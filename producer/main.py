from fastapi import FastAPI
from shared import ScrapingTask

app = FastAPI(title='Web Scraping Distributed', version='1.0')

if __name__ == '__main__' :
    import uvicorn
    uvicorn.run(app = 'main:app', host='0.0.0.0', port=8000, reload = True)