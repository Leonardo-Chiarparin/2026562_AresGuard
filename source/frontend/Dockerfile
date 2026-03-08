FROM nginx:alpine

COPY ./html/index.html /usr/share/nginx/html/index.html
COPY ./css /usr/share/nginx/html/css/
COPY ./js /usr/share/nginx/html/js/
COPY ./images /usr/share/nginx/html/images/

EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]