from flask import Flask, request, redirect, session, render_template, jsonify, send_from_directory
from flask_socketio import SocketIO, join_room, emit
from pymongo import MongoClient
from bson import ObjectId
from datetime import datetime , timedelta
from werkzeug.utils import secure_filename
import bcrypt
import re
import os
import math
import stripe
import zipfile
import uuid
import shutil
import subprocess
import json
import socket
import random
import secrets
