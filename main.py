# imports
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, HTTPException, status, Depends
from fastapi.exception_handlers import http_exception_handler, request_validation_exception_handler
# from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.exceptions import RequestValidationError
# from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from schemas import PostCreate, PostResponse, UserCreate, UserResponse, PostUpdate, UserUpdate

from typing import Annotated
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

import models
from database import Base, engine, get_db

# this looks at all models and create the tables if they don't already exists
# it is idempotent and is safe to run multiple times ( if the table already exists then nothing will happen )
# this runs on app startup
# Base.metadata.create_all(bind=engine)
# create_all is sync
@asynccontextmanager
async def lifespan(_app: FastAPI):
    # Startup
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    # Shutdown
    await engine.dispose()

app = FastAPI(lifespan=lifespan) 

app.mount("/static", StaticFiles(directory="static"), name="static")

app.mount("/media", StaticFiles(directory="media"), name="media")

templates = Jinja2Templates(directory="./templates")

# frontend html response routes

# decorator
@app.get('/', include_in_schema=False, name="root_page")
# stacking decorators
@app.get('/posts', include_in_schema=False, name="posts")
# main function
async def root_page(request: Request, db: Annotated[AsyncSession, Depends(get_db)]):
    # return {
    #     # dictionary python converts it to json
    #     "message": "Hello World!"
    # }
    # return f"<h1>{posts[0]['title']}</h1>"
    result=await db.execute(select(models.Post).options(selectinload(models.Post.author)))
    posts=result.scalars().all()
    return templates.TemplateResponse(
        request, 
        "home.html", 
        {
            "posts": posts, "title": "Home"
        }
    )

@app.get('/posts/{post_id}', include_in_schema=False)
async def get_post_page(request: Request, post_id: int, db: Annotated[AsyncSession, Depends(get_db)]):
    result=await db.execute(
        select(models.Post)
        .options(selectinload(models.Post.author))
        .where(models.Post.id==post_id)
    )
    post = result.scalars().first()
    if(post):
        title = post.title[:50]
        return templates.TemplateResponse(
            request,
            "post.html",
            {
                "post": post,
                "title": title
            }
        )
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail="Post not found"
    )

@app.get('/users/{user_id}/posts', include_in_schema=False, name="user_posts_frontend")
async def user_posts_page(request: Request, user_id: int, db: Annotated[AsyncSession, Depends(get_db)]):
    # here we don't need selectinload as there is no relationship and at the post side there a relationship that is being accessed from the user
    result=await db.execute(
        select(models.User).where(models.User.id==user_id)
    )
    user = result.scalars().first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found"
        )
    
    result = db.execute(
        select(models.Post)
        .options(selectinload(models.Post.author))
        .where(models.Post.user_id==user_id)
    )
    posts = result.scalar().all()
    if not posts:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="The specified user has no posts"
        )
    return templates.TemplateResponse(
        request,
        "user_posts.html",
        {
            "posts": posts,
            "user":user,
            "title": f"{user.username}'s Posts"
        }
    )


# Post routes

# get all posts
@app.get('/api/posts', response_model=list[PostResponse])
async def get_all_posts(db: Annotated[AsyncSession, Depends(get_db)]):
    result =await db.execute(
        select(models.Post)
        .options(selectinload(models.Post.author))
    )
    posts = result.scalars().all()
    if not posts:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No posts yet"
        )
    return posts

# get post by postId
@app.get('/api/post/{post_id}', response_model=PostResponse)
async def get_post_by_id(post_id: int, db: Annotated[AsyncSession, Depends(get_db)]):
    result =await db.execute(
        select(models.Post)
        .options(selectinload(models.Post.author))
        .where(models.Post.id==post_id)
    )
    post = result.scalars().first()
    if not post:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Specified Post not found"
        )
    return post

# update post
@app.put('/api/post/{post_id}', response_model=PostResponse)
async def update_post_full_by_id(post_id: int, post_data: PostCreate, db: Annotated[AsyncSession, Depends(get_db)]):
    result =await db.execute(
        select(models.Post)
        .options(selectinload(models.Post.author))
        .where(models.Post.id==post_id)
    )
    post = result.scalars().first()
    if not post:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Post not found"
        )
    if post_data.user_id != post.user_id:
        result=await db.execute(
            select(models.User).where(models.User.id==post_data.user_id)
        )
        user=result.scalars().first()
        if not user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User not found"
            )
    post.title=post_data.title
    post.content=post_data.content
    post.user_id=post_data.user_id

    await db.commit()
    await db.refresh(post, attribute_names=["author"])
    return post

# update post partial
@app.patch('/api/post/{post_id}', response_model=PostResponse)
async def update_post_partial_by_id(post_id: int, post_data: PostUpdate, db: Annotated[AsyncSession, Depends(get_db)]):
    result =await db.execute(
        select(models.Post)
        .options(selectinload(models.Post.author))
        .where(models.Post.id==post_id)
    )
    post = result.scalars().first()
    if not post:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Post not found"
        )
    
    # if user only updates the title and not other fields so because other fields are set default=None then other fields will become none
    # so exclude_unset=True is used
    update_data = post_data.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(post, field, value)
    
    await db.commit()
    await db.refresh(post)
    return post

# delete post
@app.delete('/api/post/{post_id}', status_code=status.HTTP_204_NO_CONTENT)
async def delete_post_by_id(post_id: int, db: Annotated[AsyncSession, Depends(get_db)]):
    result=await db.execute(
        select(models.Post)
        .options(selectinload(models.Post.author))
        .where(models.Post.id==post_id)
    )
    post=result.scalars().first()
    if not post:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Specified post not found"
        )
    
    await db.delete(post)
    await db.commit()

# create post
@app.post('/api/posts', response_model=PostResponse, status_code=status.HTTP_201_CREATED)
async def create_post(post: PostCreate, db: Annotated[AsyncSession, Depends(get_db)]):
    result=await db.execute(
        select(models.User).where(models.User.id==post.user_id)
    )
    user = result.scalars().first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found"
        )
    new_post = models.Post(
        title=post.title,
        content=post.content,
        user_id=post.user_id
    )
    db.add(new_post)
    await db.commit()
    await db.refresh(new_post, attribute_names=["author"])
    return new_post


# user routes
@app.post('/api/users', response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def create_user(user: UserCreate, db: Annotated[AsyncSession, Depends(get_db)]):
    result = await db.execute(
        select(models.User).where(models.User.username==user.username)
    )
    existing_user = result.scalars().first()

    if(existing_user):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Username already exists"
        )
    
    result = db.execute(
        select(models.User).where(models.User.email==user.email)
    )
    existing_email = result.scalars().first()

    if(existing_email):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email already exists"
        )
    
    new_user = models.User(
        username=user.username,
        email=user.email
    )

    db.add(new_user)
    await db.commit()
    await db.refresh(new_user)

    return new_user


@app.get('/api/users/{user_id}', response_model=UserResponse)
async def get_user_by_id(user_id: int, db: Annotated[AsyncSession, Depends(get_db)]):
    result = db.execute(
        select(models.User).where(models.User.id==user_id)
    )
    user = result.scalars().first()

    if(user):
        return user
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND, detail="User not found"
    )


@app.get('/api/users/{user_id}/posts', response_model=list[PostResponse])
async def get_posts_created_by_specific_user(user_id: int, db: Annotated[AsyncSession, Depends(get_db)]):
    result= db.execute(
        select(models.User).where(models.User.id==user_id)
    )
    user = result.scalars().first()

    if(not user):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,detail="User not found"
        )
    
    result = db.execute(
        select(models.Post)
        .options(selectinload(models.Post.author))
        .where(models.Post.user_id==user_id)
    )
    posts = result.scalars().all()
    if(not posts):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No posts by the user yet"
        )
    return posts

@app.patch('/api/users/{user_id}', response_model=UserResponse)
async def user_partial_update(user_id: int, user_update: UserUpdate, db: Annotated[AsyncSession, Depends(get_db)]):
    result=db.execute(
        select(models.User).where(models.User.id==user_id)
    )
    user=result.scalars().first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Specified user not found"
        )
    if user_update.username is not None and user_update.username!=user.username:
        result=await db.execute(
            select(models.User).where(models.User.username==user_update.username)
        )
        existing_user=result.scalars().first()
        if existing_user:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Username already exists"
            )
    if user_update.email is not None and user_update.email != user.email:
        result = await db.execute(
            select(models.User).where(models.User.email == user_update.email),
        )
        existing_email = result.scalars().first()
        if existing_email:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Email already registered",
            )

    if user_update.username is not None:
        user.username = user_update.username
    if user_update.email is not None:
        user.email = user_update.email
    if user_update.image_file is not None:
        user.image_file = user_update.image_file

    await db.commit()
    await db.refresh(user)
    return user

@app.delete('/api/users/{user_id}', status_code=status.HTTP_204_NO_CONTENT)
async def user_delete(user_id: int, db: Annotated[AsyncSession, Depends(get_db)]):
    result=await db.execute(
        select(models.User).where(models.User.id==user_id)
    )
    user=result.scalars().first()

    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Specified user not found"
        )
    
    await db.delete(user)
    await db.commit()


@app.exception_handler(StarletteHTTPException)
async def general_http_exception_handler(request: Request, exception: StarletteHTTPException):

    if request.url.path.startswith("/api"):
        # return JSONResponse(
        #     status_code=exception.status_code,
        #     content={"detail": message},
        # )
        return await http_exception_handler(
            request,
            exception
        )
    
    message = (
        exception.detail
        if exception.detail
        else "An error occurred. Please check your request and try again."
    )

    return templates.TemplateResponse(
        request,
        "error.html",
        {
            "status_code": exception.status_code,
            "title": exception.status_code,
            "message": message,
        },
        status_code=exception.status_code,
    )

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exception: RequestValidationError):
    if request.url.path.startswith("/api"):
        # return JSONResponse(
        #     status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        #     content={"detail": exception.errors()},
        # )
        return await request_validation_exception_handler(
            request,
            exception
        )

    return templates.TemplateResponse(
        request,
        "error.html",
        {
            "status_code": status.HTTP_422_UNPROCESSABLE_CONTENT,
            "title": status.HTTP_422_UNPROCESSABLE_CONTENT,
            "message": "Invalid request. Please check your input and try again.",
        },
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
    )