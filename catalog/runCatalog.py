
import os
import json
import logging
import pymongo
import requests
import ConfigParser

from time import sleep
from bson import json_util, ObjectId
from flask import Flask, jsonify, Response, request, abort

from Bundle import Bundle
from Plugin import Plugin

app = Flask(__name__)

currentDir = os.path.dirname(os.path.realpath(__file__))

config = ConfigParser.ConfigParser()
config.read('catalog.cfg')

resourcesPath = os.path.join(currentDir, config.get('RESOURCES', 'resourcesDirectory'))
if not os.path.exists(resourcesPath):
    os.makedirs(resourcesPath)

client = pymongo.MongoClient(config.get('MONGODB', 'hostname'), config.getint('MONGODB', 'port'))
db = client.__getattr__(config.get('MONGODB', 'dbName'))
bundleTable = db.__getattr__(config.get('MONGODB', 'bundleTable'))
pluginTable = db.__getattr__(config.get('MONGODB', 'pluginTable'))
resourceTable = db.__getattr__(config.get('MONGODB', 'resourceTable'))

uriAnalyser = config.get('ANALYSER', 'uri')

bundleRootPath = config.get('CATALOG', 'bundleStore')
if not os.path.exists(bundleRootPath):
    os.makedirs(bundleRootPath)

@app.route("/")
def index():
    return "test catalog with mongodb"


def mongodoc_jsonify(*args, **kwargs):
    return Response(json.dumps(args[0], default=json_util.default), mimetype='application/json')

@app.route("/bundle", methods=["POST"])
def newBundle():
    bundleName = request.get_json().get('bundleName', None)
    bundleDescription = request.get_json().get('bundleDescription', None)
    userId = request.get_json().get('userId', None)
    companyId = request.get_json().get('companyId', None)

    bundleId = bundleTable.count()

    if  bundleId == None or bundleName == None or userId == None:
        app.logger.error("bundleName, bundleId or userId is undefined")
        abort(404)

    bundle = Bundle(bundleId, bundleName, userId)
    bundle.companyId = companyId
    bundle.description = bundleDescription

    bundleTable.insert(bundle.__dict__)

    requestResult = bundleTable.find_one({"bundleId": bundleId})
    return mongodoc_jsonify(requestResult)

@app.route("/bundle")
def getBundles():
    count = int(request.args.get('count', 20))
    skip = int(request.args.get('skip', 0))
    bundle = bundleTable.find().limit(count).skip(skip)
    return mongodoc_jsonify({"bundles":[ result for result in bundle ]})

@app.route("/bundle/<int:bundleId>")
def getBundle(bundleId):
    bundle = bundleTable.find_one({"bundleId": bundleId})
    if bundle == None:
        app.logger.error("No matching bundle has been found")
        abort(404)
    return mongodoc_jsonify(bundle)


@app.route('/bundle/<int:bundleId>/archive', methods=['POST', 'PUT'])
def uploadArchive(bundleId):
    bundle = bundleTable.find_one({"bundleId": bundleId})

    if bundle == None:
        logging.error("No matching bundle has been found")
        abort(400)

    mappingExtension = {
        "application/zip": ".zip",
        "application/gzip": ".tar.gz"
    }

    # if request.headers['content-type'] not in mappingExtension:
    #     app.logger.error("Format is not supported : " + str(request.headers['content-type']))
    #     abort(400)

    #extension = mappingExtension[ request.headers['content-type'] ]
    extension = ".tar.gz"

    archivePath = os.path.join(bundleRootPath, str(bundleId) + extension)

    try:
        file = request.files['file']
        file.save(archivePath)
    except Exception, err:
        logging.error(err)
        abort(400)

    bundle["archivePath"] = archivePath
    bundleTable.update({'_id': bundle['_id']}, bundle)
    return mongodoc_jsonify(bundle)


@app.route('/bundle/<int:bundleId>/analyse', methods=['POST'])
def analyseBundle(bundleId):
    bundle = bundleTable.find_one({"bundleId": bundleId})

    if bundle == None:
        app.logger.error("No matching bundle has been found")
        abort(400)

    if bundle["archivePath"] == None: 
        app.logger.error("The bundle as no directory path")
        abort(400)
    

    headers = {'content-type': 'application/gzip'}
    analyseReturn = requests.post(uriAnalyser+"/bundle/"+str(bundleId), data=open(bundle["archivePath"], 'r').read(), headers=headers)

    pluginIdOffset = pluginTable.count()

    ofxPropList = {"OfxPropShortLabel", "OfxPropLongLabel"}
    bundleData = analyseReturn.json()['datas']

    while 1:
        analyseReturn = requests.get(uriAnalyser+"/bundle/"+str(bundleId)).json()

        if analyseReturn['status'] == "done":
            bundleData = analyseReturn['datas']
            break
        sleep(1)

    for index, plugin in enumerate(bundleData['plugins']) :
        pluginId = pluginIdOffset + index
        currentPlugin = Plugin(pluginId, bundleId)
        currentPlugin.clips = plugin['clips']
        currentPlugin.parameters = plugin['parameters']
        currentPlugin.properties = plugin['properties']
        currentPlugin.rawIdentifier = plugin['rawIdentifier']
        currentPlugin.uri = plugin['uri']
        currentPlugin.version = plugin['version']

        for prop in plugin['properties']:
            name = prop['name']
            if name in ofxPropList :
                value = prop['value']

                if name == "OfxPropShortLabel":
                    currentPlugin.shortName = value[0]

                if name == "OfxPropLongLabel":
                    currentPlugin.name = value[0]

        bundle['plugins'].append(pluginId)
        pluginTable.insert(currentPlugin.__dict__)

    return mongodoc_jsonify(bundle)

@app.route("/bundle/<int:bundleId>", methods=["DELETE"])
def deleteBundle(bundleId):
    bundle = bundleTable.find_one({"bundleId": bundleId})
    if bundle == None:
        abort(404)

    for pluginId in bundle.plugins:
        deleteStatus = pluginTable.remove({"pluginId":pluginId})
        if deleteStatus['n'] == 0:
            abort(404)

    deleteStatus = bundleTable.remove({"bundleId":bundleId})

    if deleteStatus['n'] == 0:
        abort(404)

    return jsonify(**deleteStatus)

@app.route("/bundle/<int:bundleId>/plugin", methods=['POST'])
def newPlugin(bundleId):
    pluginId = request.get_json().get('pluginId', None)
    pluginName = request.get_json().get('name', None)

    if pluginId == None or pluginName == None:
        abort(404)

    plugin = Plugin(pluginId, bundleId, pluginName)
    
    pluginTable.insert(plugin.__dict__)

    requestResult = pluginTable.find_one({"pluginId": pluginId})
    return mongodoc_jsonify(requestResult)


@app.route("/bundle/<int:bundleId>/plugin")
def getPlugins(bundleId):
    count = int(request.args.get('count', 20))
    skip = int(request.args.get('skip', 0))
    plugin = pluginTable.find({"bundleId":bundleId}).limit(count).skip(skip)
    return mongodoc_jsonify({"plugins":[ result for result in plugin ]})

@app.route("/plugin")
def getAllPlugins():
    #Text search
    keyWord = request.args.get('keyWord', None)
    
    #Alphabetical sorting
    alphaSort = request.args.get('alphaSort', None)

    if alphaSort != None :
        if alphaSort != 'asc' and alphaSort != 'desc' :
            alphaSort = None
        else : 
            if alphaSort == 'asc' :
                alphaSort = 1
            if alphaSort == 'desc' :
                alphaSort = -1

    count = int(request.args.get('count', 20))
    skip = int(request.args.get('skip', 0))


    if keyWord != None :
        return textSearchPlugin(keyWord, count)

    else :
        plugin = pluginTable.find().sort('name' , alphaSort).limit(count).skip(skip)
        return mongodoc_jsonify({"plugins":[ result for result in plugin ]})


def textSearchPlugin(keyWord, count):
    #To Do Tags
    text_results = db.command('text', config.get('MONGODB', 'pluginTable'), search = keyWord, limit=count)
    plugins = [ result['obj'] for result in text_results['results'] ]
    return mongodoc_jsonify({"plugins": plugins})


@app.route("/bundle/<int:bundleId>/plugin/<int:pluginId>")
@app.route("/plugin/<int:pluginId>")
def getPlugin(pluginId, bundleId=None):
    plugin = pluginTable.find_one({"pluginId": pluginId})
    if plugin == None:
        abort(404)

    return mongodoc_jsonify(plugin)

@app.route('/resources', methods=['POST'])
def addResource():
    '''
    Upload resource file on the database
    '''

    mimetype = request.mimetype
    name = ""
    size = request.content_length

    if not mimetype:
        app.logger.error("Invalide resource.")
        abort(404)

    uid = resourceTable.insert({ 
        "mimetype" : mimetype,
        "size" : size,
        "name" : name})

    img = request.data


    imgFile = os.path.join(resourcesPath, str(uid))
    file = request.files['file']
    file.save(imgFile)

    resource = resourceTable.find_one({ "_id" : ObjectId(uid)})
    return mongodoc_jsonify(resource)

@app.route('/resources', methods=['GET'])
def getResources():
    '''
    Returns resource file from db.
    '''

    count = int(request.args.get('count', 10))
    skip = int(request.args.get('skip', 0))
    resources = resourceTable.find().limit(count).skip(skip)
    return mongodoc_jsonify({"resources":[ result for result in resources ]})

@app.route('/resources/<resourceId>', methods=['GET'])
def getResourceById(resourceId):
    '''
    Returns resource datas from db.
    '''
    resourceData = resourceTable.find_one({ "_id" : ObjectId(resourceId)})

    if resourceData == None:
        abort(404)
    return mongodoc_jsonify(resourceData)


@app.route('/resources/<resourceId>/data', methods=['GET'])
def getResourceData(resourceId):
    '''
     Returns the resource.
    '''

    resourceData = resourceTable.find_one({ "_id" : ObjectId(resourceId)})
    if not resourceData:
        abort(404)

    filePath = os.path.join (resourcesPath, resourceId)

    if not os.path.isfile(filePath):
        abort(404)

    resource = open(filePath)
    return Response(resource.read(), mimetype=resourceData['mimetype'])


@app.route("/plugin/<int:pluginId>/images", methods= ['POST'])
def addImageToPlugin(pluginId):
    if "ressourceId" not in request.get_json() :
        abort(404)

    imageId = request.get_json()["ressourceId"]


    plugin = pluginTable.find_one({"pluginId": pluginId})
    if plugin == None:
        abort(404)

    pluginTable.update({"pluginId" : pluginId}, { '$addToSet' : {"sampleImagesPath" : imageId} }, upsert=True)
    plugin = pluginTable.find_one({"pluginId": pluginId})
    
    return mongodoc_jsonify(plugin)

#TO DO : Tags
pluginTable.ensure_index([
        ('name', 'text'),
        ('description', 'text'),
        ('shortDescription', 'text'),
    ],
    name = "search_index",
    weights={
        'name':100,
        'description':20,
        'shortDescription':60
    }
)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5002 ,debug=True)

